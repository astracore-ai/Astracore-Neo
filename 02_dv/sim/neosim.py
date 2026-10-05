#!/usr/bin/env python3
"""
neosim -- a small cycle-based simulator for the synthesizable SystemVerilog subset
used in the AstraCore Neo RTL (v0.1, 2026-10-04).

Why it exists: the authoring sandbox has no HDL simulator and no network, so the RTL
could not be executed. neosim parses the actual .sv files, elaborates parameters and
generate blocks, flattens the hierarchy into cells and processes, and simulates with
two-phase (evaluate / commit) semantics for always_ff and fixpoint evaluation for
continuous assignments and always_comb. It is a bring-up tool: Cadence Xcelium stays
the sign-off simulator. If neosim and Xcelium ever disagree, Xcelium is right.

Supported subset
  module ... endmodule, #(parameter int P = expr), ANSI ports
  input/output logic [signed] [W-1:0] name [N] ..., localparam, logic declarations
  assign, always_ff @(posedge clk [or negedge rst_n]), always_comb
  if/else, for (int|genvar), begin/end with labels, generate for/if
  module instantiation with #(.P(v)) and .port(expr); unpacked-array ports connected
  whole or per element
  operators + - * / % == != < <= > >= && || ! ~ & | ^ << >> ?: , indexing [i],
  concatenation {a,b}, replication {N{a}}, size casts N'(x), fill literals '0 '1,
  sized/unsized literals, $clog2, $signed, $unsigned
Not supported: tasks, initial, delays, strings, part-selects [a:b] on vectors,
interfaces, packages, multiple clocks (one clock named by the always_ff is assumed).

Semantics implemented (IEEE 1800 essentials): expression signedness is signed only if
every operand is signed; operands are extended to the assignment context; arithmetic is
unbounded then truncated to the target width on assignment; comparisons and logical
operators are self-determined and yield unsigned 1-bit results.
"""
from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# ----------------------------------------------------------------------------
# Lexer
# ----------------------------------------------------------------------------
TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<lcomment>//[^\n]*)
  | (?P<bcomment>/\*.*?\*/)
  | (?P<sized>\d+\s*'[sS]?[bBdDhHoO]\s*[0-9a-fA-F_xXzZ?]+)
  | (?P<fill>'[01])
  | (?P<num>\d[\d_]*)
  | (?P<ident>\$?[A-Za-z_][A-Za-z_0-9$]*)
  | (?P<op>-:|\+:|<<<|>>>|<<=|>>=|<=|>=|==|!=|&&|\|\||\+\+|--|\+=|-=|<<|>>|[-+*/%<>=!~&|^?:()\[\]{};,.#@'])
""", re.VERBOSE | re.DOTALL)

KEYWORDS = {"module", "endmodule", "parameter", "localparam", "input", "output", "logic", "signed",
            "int", "genvar", "assign", "always_ff", "always_comb", "posedge", "negedge", "or",
            "if", "else", "for", "begin", "end", "generate", "endgenerate", "wire", "reg",
            "unsigned", "integer", "case", "casez", "casex", "endcase", "default"}


@dataclass
class Tok:
    kind: str
    val: str
    line: int


def lex(src: str, fname: str = "") -> List[Tok]:
    toks, pos, line = [], 0, 1
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m:
            raise SyntaxError(f"{fname}:{line}: unexpected character {src[pos]!r}")
        kind = m.lastgroup
        text = m.group(0)
        if kind in ("ws", "lcomment", "bcomment"):
            line += text.count("\n")
        elif kind == "ident" and text in KEYWORDS:
            toks.append(Tok("kw", text, line))
        else:
            toks.append(Tok(kind, text, line))
        pos = m.end()
    toks.append(Tok("eof", "", line))
    return toks


# ----------------------------------------------------------------------------
# AST (tuples): expressions
#   ('num', value, width, signed) ('fill', bit) ('ident', name) ('index', base, idx)
#   ('binop', op, a, b) ('unop', op, a) ('tern', c, a, b) ('cast', width_expr, a)
#   ('concat', [e]) ('repl', n, e) ('call', name, [args])
# statements
#   ('block', [s]) ('if', c, s, s_or_None) ('for', var, init, cond, step_var, step_op, step_expr, body)
#   ('assign', lvalue, expr, blocking)
# module items
#   ('localparam', name, expr) ('decl', signed, rng, [(name, dims)]) ('assign', lv, e)
#   ('always_ff', clk, rst, stmt) ('always_comb', stmt)
#   ('genfor', var, init, cond, step, label, items) ('genif', cond, lbl1, items1, lbl2, items2)
#   ('inst', modname, [(p, e)], instname, [(port, e)])
# ----------------------------------------------------------------------------
@dataclass
class ModuleDef:
    name: str
    params: List[Tuple[str, Any]]
    ports: List[Tuple[str, bool, Any, str, List[Any]]]  # dir, signed, rng, name, dims
    items: List[Any]


class Parser:
    def __init__(self, toks: List[Tok], fname: str = ""):
        self.t, self.i, self.fname = toks, 0, fname

    # -- helpers --
    def peek(self, k=0) -> Tok:
        return self.t[self.i + k]

    def err(self, msg):
        tk = self.peek()
        raise SyntaxError(f"{self.fname}:{tk.line}: {msg} (near {tk.val!r})")

    def accept(self, val=None, kind=None) -> Optional[Tok]:
        tk = self.peek()
        if (val is None or tk.val == val) and (kind is None or tk.kind == kind):
            self.i += 1
            return tk
        return None

    def expect(self, val=None, kind=None) -> Tok:
        tk = self.accept(val, kind)
        if tk is None:
            self.err(f"expected {val or kind}")
        return tk

    def ident(self) -> str:
        return self.expect(kind="ident").val

    # -- top --
    def parse_file(self) -> List[ModuleDef]:
        mods = []
        while self.peek().kind != "eof":
            mods.append(self.parse_module())
        return mods

    def parse_module(self) -> ModuleDef:
        self.expect("module")
        name = self.ident()
        params = []
        if self.accept("#"):
            self.expect("(")
            while not self.accept(")"):
                if self.peek().val == "parameter":
                    self.i += 1
                    self.accept("int") or self.accept("integer") or self.accept("logic")
                    self.accept("signed") or self.accept("unsigned")
                    self.parse_range_opt()
                pname = self.ident()                 # "parameter int A = 1, B = 2" continues without the keyword
                self.expect("=")
                params.append((pname, self.parse_expr()))
                self.accept(",")
        ports = []
        self.expect("(")
        d, signed, rng = None, False, None
        while not self.accept(")"):
            if self.peek().kind == "kw" and self.peek().val in ("input", "output"):
                d = self.expect(kind="kw").val
                self.accept("logic") or self.accept("wire") or self.accept("reg")
                signed = bool(self.accept("signed"))
                rng = self.parse_range_opt()
            elif d is None:
                self.err("expected input/output")
            pname = self.ident()                     # "input logic a, b" continues with the same kind
            dims = self.parse_dims()
            ports.append((d, signed, rng, pname, dims))
            self.accept(",")
        self.expect(";")
        items = []
        while not self.accept("endmodule"):
            items.extend(self.parse_item())
        return ModuleDef(name, params, ports, items)

    def parse_range_opt(self):
        if self.peek().val == "[":
            self.expect("[")
            msb = self.parse_expr()
            self.expect(":")
            lsb = self.parse_expr()
            self.expect("]")
            return (msb, lsb)
        return None

    def parse_dims(self):
        dims = []
        while self.peek().val == "[":
            self.expect("[")
            a = self.parse_expr()
            if self.accept(":"):
                b = self.parse_expr()
                dims.append(("range", a, b))
            else:
                dims.append(("size", a))
            self.expect("]")
        return dims

    # -- module items --
    def parse_item(self) -> List[Any]:
        tk = self.peek()
        if tk.val == "localparam" or tk.val == "parameter":
            self.i += 1
            self.accept("int") or self.accept("integer") or self.accept("logic")
            self.accept("signed") or self.accept("unsigned")
            self.parse_range_opt()
            items = []
            while True:
                name = self.ident()
                self.expect("=")
                items.append(("localparam", name, self.parse_expr()))
                if not self.accept(","):
                    break
            self.expect(";")
            return items
        if tk.val in ("logic", "wire", "reg"):
            self.i += 1
            signed = bool(self.accept("signed"))
            rng = self.parse_range_opt()
            names = []
            while True:
                n = self.ident()
                dims = self.parse_dims()
                names.append((n, dims))
                if not self.accept(","):
                    break
            self.expect(";")
            return [("decl", signed, rng, names)]
        if tk.val == "assign":
            self.i += 1
            lv = self.parse_lvalue()
            self.expect("=")
            e = self.parse_expr()
            self.expect(";")
            return [("assign", lv, e)]
        if tk.val == "always_ff":
            self.i += 1
            self.expect("@")
            self.expect("(")
            self.expect("posedge")
            clk = self.ident()
            rst = None
            if self.accept("or"):
                self.expect("negedge")
                rst = self.ident()
            self.expect(")")
            return [("always_ff", clk, rst, self.parse_stmt())]
        if tk.val == "always_comb":
            self.i += 1
            return [("always_comb", self.parse_stmt())]
        if tk.val == "generate":
            self.i += 1
            items = []
            while not self.accept("endgenerate"):
                items.extend(self.parse_item())
            return items
        if tk.val == "for":
            return [self.parse_genfor()]
        if tk.val == "if":
            return [self.parse_genif()]
        if tk.kind == "ident":
            return [self.parse_inst()]
        self.err("unexpected module item")

    def parse_genfor(self):
        self.expect("for")
        self.expect("(")
        self.expect("genvar")
        var = self.ident()
        self.expect("=")
        init = self.parse_expr()
        self.expect(";")
        cond = self.parse_expr()
        self.expect(";")
        v2 = self.ident()
        if v2 != var:
            self.err("genvar step variable mismatch")
        if self.accept("++"):
            step = ("binop", "+", ("ident", var), ("num", 1, 32, True))
        elif self.accept("+="):
            step = ("binop", "+", ("ident", var), self.parse_expr())
        else:
            self.expect("=")
            step = self.parse_expr()
        self.expect(")")
        label, items = self.parse_gen_block()
        return ("genfor", var, init, cond, step, label, items)

    def parse_gen_block(self):
        self.expect("begin")
        label = None
        if self.accept(":"):
            label = self.ident()
        items = []
        while not self.accept("end"):
            items.extend(self.parse_item())
        return label, items

    def parse_genif(self):
        self.expect("if")
        self.expect("(")
        cond = self.parse_expr()
        self.expect(")")
        l1, items1 = self.parse_gen_block()
        l2, items2 = None, []
        if self.accept("else"):
            if self.peek().val == "if":
                items2 = [self.parse_genif()]
            else:
                l2, items2 = self.parse_gen_block()
        return ("genif", cond, l1, items1, l2, items2)

    def parse_inst(self):
        modname = self.ident()
        params = []
        if self.accept("#"):
            self.expect("(")
            while not self.accept(")"):
                self.expect(".")
                p = self.ident()
                self.expect("(")
                params.append((p, self.parse_expr()))
                self.expect(")")
                self.accept(",")
        instname = self.ident()
        self.expect("(")
        conns = []
        while not self.accept(")"):
            self.expect(".")
            p = self.ident()
            self.expect("(")
            e = None if self.peek().val == ")" else self.parse_expr()
            self.expect(")")
            conns.append((p, e))
            self.accept(",")
        self.expect(";")
        return ("inst", modname, params, instname, conns)

    # -- statements --
    def parse_stmt(self):
        tk = self.peek()
        if tk.val == ";":                      # empty statement (e.g. "default: ;")
            self.i += 1
            return ("block", [])
        if tk.val == "begin":
            self.i += 1
            if self.accept(":"):
                self.ident()
            stmts = []
            while not self.accept("end"):
                stmts.append(self.parse_stmt())
            return ("block", stmts)
        if tk.val == "if":
            self.i += 1
            self.expect("(")
            c = self.parse_expr()
            self.expect(")")
            s1 = self.parse_stmt()
            s2 = self.parse_stmt() if self.accept("else") else None
            return ("if", c, s1, s2)
        if tk.val == "for":
            self.i += 1
            self.expect("(")
            self.accept("int") or self.accept("integer")
            var = self.ident()
            self.expect("=")
            init = self.parse_expr()
            self.expect(";")
            cond = self.parse_expr()
            self.expect(";")
            sv = self.ident()
            if self.accept("++"):
                sop, sexpr = "+", ("num", 1, 32, True)
            elif self.accept("--"):
                sop, sexpr = "-", ("num", 1, 32, True)
            elif self.accept("+="):
                sop, sexpr = "+", self.parse_expr()
            elif self.accept("-="):
                sop, sexpr = "-", self.parse_expr()
            else:
                self.expect("=")
                sop, sexpr = "=", self.parse_expr()
            self.expect(")")
            body = self.parse_stmt()
            return ("for", var, init, cond, sv, sop, sexpr, body)
        if tk.val in ("int", "integer", "logic"):
            self.i += 1
            self.accept("signed") or self.accept("unsigned")
            self.parse_range_opt()
            names = [self.ident()]
            while self.accept(","):
                names.append(self.ident())
            self.expect(";")
            return ("local", names)
        if tk.val in ("case", "casez", "casex"):
            self.i += 1
            self.expect("(")
            sel = self.parse_expr()
            self.expect(")")
            arms, default = [], None
            while not self.accept("endcase"):
                if self.accept("default"):
                    self.accept(":")
                    default = self.parse_stmt()
                    continue
                labels = [self.parse_expr()]
                while self.accept(","):
                    labels.append(self.parse_expr())
                self.expect(":")
                arms.append((labels, self.parse_stmt()))
            return ("case", sel, arms, default)
        lv = self.parse_lvalue()
        if self.accept("<="):
            blocking = False
        else:
            self.expect("=")
            blocking = True
        e = self.parse_expr()
        self.expect(";")
        return ("assign", lv, e, blocking)

    def parse_lvalue(self):
        name = self.ident()
        node = ("ident", name)
        while self.peek().val == "[":
            self.expect("[")
            idx = self.parse_expr()
            if self.accept(":"):
                lsb = self.parse_expr()
                self.expect("]")
                return ("part", node, idx, lsb)
            if self.accept("-:"):
                w = self.parse_expr()
                self.expect("]")
                return ("partidx", node, idx, w, "-")
            if self.accept("+:"):
                w = self.parse_expr()
                self.expect("]")
                return ("partidx", node, idx, w, "+")
            self.expect("]")
            node = ("index", node, idx)
        return node

    # -- expressions (precedence climbing) --
    BINPREC = {"||": 1, "&&": 2, "|": 3, "^": 4, "&": 5, "==": 6, "!=": 6,
               "<": 7, "<=": 7, ">": 7, ">=": 7, "<<": 8, ">>": 8, "<<<": 8, ">>>": 8, "+": 9, "-": 9,
               "*": 10, "/": 10, "%": 10}

    def parse_expr(self):
        return self.parse_ternary()

    def parse_ternary(self):
        c = self.parse_binary(1)
        if self.accept("?"):
            a = self.parse_ternary()
            self.expect(":")
            b = self.parse_ternary()
            return ("tern", c, a, b)
        return c

    def parse_binary(self, minprec):
        lhs = self.parse_unary()
        while True:
            op = self.peek().val
            prec = self.BINPREC.get(op)
            if prec is None or prec < minprec or self.peek().kind != "op":
                return lhs
            self.i += 1
            rhs = self.parse_binary(prec + 1)
            lhs = ("binop", op, lhs, rhs)

    def parse_unary(self):
        tk = self.peek()
        if tk.kind == "op" and tk.val in ("!", "~", "-", "+", "^", "&", "|"):
            self.i += 1
            a = self.parse_unary()
            if tk.val == "+":
                return a
            if tk.val in ("^", "&", "|"):
                return ("reduce", tk.val, a)
            return ("unop", tk.val, a)
        return self.parse_postfix()

    def parse_postfix(self):
        node = self.parse_primary()
        while self.peek().val == "[":
            self.expect("[")
            idx = self.parse_expr()
            if self.accept(":"):
                lsb = self.parse_expr()
                self.expect("]")
                node = ("part", node, idx, lsb)
                continue
            if self.accept("-:"):
                w = self.parse_expr()
                self.expect("]")
                node = ("partidx", node, idx, w, "-")
                continue
            if self.accept("+:"):
                w = self.parse_expr()
                self.expect("]")
                node = ("partidx", node, idx, w, "+")
                continue
            self.expect("]")
            node = ("index", node, idx)
        return node

    def parse_primary(self):
        tk = self.peek()
        if tk.kind == "sized":
            self.i += 1
            return self.sized_literal(tk.val)
        if tk.kind == "fill":
            self.i += 1
            return ("fill", int(tk.val[1]))
        if tk.kind == "num":
            self.i += 1
            if self.peek().val == "'" and self.peek(1).val == "(":
                self.i += 2
                e = self.parse_expr()
                self.expect(")")
                return ("cast", ("num", int(tk.val.replace("_", "")), 32, True), e)
            return ("num", int(tk.val.replace("_", "")), 32, True)
        if tk.kind == "ident":
            self.i += 1
            if self.peek().val == "'" and self.peek(1).val == "(":
                self.i += 2
                e = self.parse_expr()
                self.expect(")")
                return ("cast", ("ident", tk.val), e)
            if self.peek().val == "(":
                self.expect("(")
                args = []
                while not self.accept(")"):
                    args.append(self.parse_expr())
                    self.accept(",")
                return ("call", tk.val, args)
            return ("ident", tk.val)
        if tk.val == "(":
            self.i += 1
            e = self.parse_expr()
            self.expect(")")
            return e
        if tk.val == "{":
            self.i += 1
            first = self.parse_expr()
            if self.peek().val == "{":
                self.expect("{")
                inner = [self.parse_expr()]
                while self.accept(","):
                    inner.append(self.parse_expr())
                self.expect("}")
                self.expect("}")
                return ("repl", first, ("concat", inner))
            items = [first]
            while self.accept(","):
                items.append(self.parse_expr())
            self.expect("}")
            return ("concat", items)
        self.err("unexpected token in expression")

    @staticmethod
    def sized_literal(text):
        m = re.match(r"(\d+)\s*'([sS]?)([bBdDhHoO])\s*([0-9a-fA-F_xXzZ?]+)", text)
        width = int(m.group(1))
        signed = m.group(2) != ""
        base = {"b": 2, "d": 10, "h": 16, "o": 8}[m.group(3).lower()]
        digits = m.group(4).replace("_", "").lower().replace("x", "0").replace("z", "0").replace("?", "0")
        val = int(digits, base) & ((1 << width) - 1)
        return ("num", val, width, signed)


# ----------------------------------------------------------------------------
# Elaboration
# ----------------------------------------------------------------------------
class Cell:
    __slots__ = ("v", "w", "s", "name")

    def __init__(self, w, s, name):
        self.v, self.w, self.s, self.name = 0, w, s, name


@dataclass
class SigDecl:
    hier: str
    width: int
    signed: bool
    dims: List[Tuple[int, int]]  # (lo, size) per unpacked dim
    is_port: Optional[str] = None  # 'input' / 'output' / None


class UnionFind:
    def __init__(self):
        self.p: Dict[str, str] = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


class Scope:
    def __init__(self, parent: Optional["Scope"], prefix: str):
        self.parent, self.prefix, self.names = parent, prefix, {}

    def lookup(self, name):
        s = self
        while s is not None:
            if name in s.names:
                return s.names[name]
            s = s.parent
        raise NameError(f"unresolved identifier {name!r} in {self.prefix}")


class Design:
    """Holds elaborated declarations, alias relations and process ASTs; then builds cells."""

    def __init__(self, modules: Dict[str, ModuleDef]):
        self.modules = modules
        self.sigs: Dict[str, SigDecl] = {}
        self.uf = UnionFind()
        self.comb: List[Tuple[Any, Any, int]] = []      # (lvalue_resolved, expr_resolved, line)
        self.comb_blocks: List[Any] = []                   # resolved stmts
        self.ff_blocks: List[Tuple[Any, Any, Any]] = []    # (clk_hier, rst_hier, stmt)
        self.top_ports: Dict[str, str] = {}                # port name -> hier

    # -- entry --
    def elaborate(self, top: str, params: Optional[Dict[str, int]] = None, path="top"):
        self.top_ports = {}
        self._elab_module(top, params or {}, path, None, is_top=True)
        self._build_cells()

    # -- constant evaluation of parameter expressions --
    def _const(self, e, scope: Scope) -> int:
        r = self._resolve(e, scope)
        v = const_fold(r)
        if v is None:
            raise ValueError(f"expression is not constant in {scope.prefix}: {e}")
        return v

    def _dims(self, dims, scope: Scope):
        out = []
        for d in dims:
            if d[0] == "size":
                out.append((0, self._const(d[1], scope)))
            else:
                a, b = self._const(d[1], scope), self._const(d[2], scope)
                out.append((min(a, b), abs(a - b) + 1))
        return out

    def _width(self, rng, scope: Scope):
        if rng is None:
            return 1
        msb, lsb = self._const(rng[0], scope), self._const(rng[1], scope)
        return abs(msb - lsb) + 1

    def _declare(self, scope: Scope, name, width, signed, dims, is_port=None) -> SigDecl:
        hier = f"{scope.prefix}.{name}"
        decl = SigDecl(hier, width, signed, dims, is_port)
        self.sigs[hier] = decl
        scope.names[name] = decl
        return decl

    def _elab_module(self, modname, params: Dict[str, int], path: str, port_conns, is_top=False):
        m = self.modules.get(modname)
        if m is None:
            raise NameError(f"module {modname!r} not found")
        scope = Scope(None, path)
        # parameters: overrides then defaults, in declared order (defaults may use earlier params)
        for pname, pexpr in m.params:
            scope.names[pname] = params[pname] if pname in params else self._const(pexpr, scope)
        # ports
        for d, signed, rng, pname, dims in m.ports:
            decl = self._declare(scope, pname, self._width(rng, scope), signed, self._dims(dims, scope), d)
            if is_top:
                self.top_ports[pname] = decl.hier
        if port_conns is not None:
            for pname, conn in port_conns:
                if conn is None:
                    continue
                decl = scope.names[pname]
                self._connect(decl, conn)
        self._elab_items(m.items, scope)

    def _connect(self, child: SigDecl, conn_resolved):
        """conn_resolved is a resolved expression in the parent's scope."""
        ref = as_ref(conn_resolved)
        if ref is not None:
            pdecl, idx = ref
            if idx is None and pdecl.dims == child.dims and pdecl.width == child.width:
                for key in element_keys(child):
                    self.uf.union(child.hier + key, pdecl.hier + key)
                return
            if idx is not None and len(idx) == len(pdecl.dims) and not child.dims and pdecl.width == child.width:
                self.uf.union(child.hier, pdecl.hier + "[" + ",".join(map(str, idx)) + "]")
                return
            if idx is not None and len(idx) < len(pdecl.dims) and pdecl.dims[len(idx):] == child.dims \
                    and pdecl.width == child.width:
                for key in element_keys(child):
                    sub = key[1:-1]
                    self.uf.union(child.hier + key, pdecl.hier + "[" + ",".join(map(str, idx)) + ("," + sub if sub else "") + "]")
                return
        if child.is_port == "input":
            self.comb.append((("sig", child, ()), conn_resolved, 0))
        else:
            raise ValueError(f"output port {child.hier} must connect to a signal or element")

    def _elab_items(self, items, scope: Scope):
        for it in items:
            kind = it[0]
            if kind == "localparam":
                scope.names[it[1]] = self._const(it[2], scope)
            elif kind == "decl":
                _, signed, rng, names = it
                w = self._width(rng, scope)
                for n, dims in names:
                    self._declare(scope, n, w, signed, self._dims(dims, scope))
            elif kind == "assign":
                lv = self._resolve(it[1], scope)
                rhs = self._resolve(it[2], scope)
                lref = as_ref(lv)
                rref = as_ref(rhs)
                # alias when both sides are plain signals / elements of equal shape
                if lref and rref:
                    (ld, li), (rd, ri) = lref, rref
                    if li is None and ri is None and ld.dims == rd.dims and ld.width == rd.width and ld.signed == rd.signed:
                        for key in element_keys(ld):
                            self.uf.union(ld.hier + key, rd.hier + key)
                        continue
                    if li is not None and ri is not None and ld.width == rd.width and ld.signed == rd.signed \
                            and len(li) == len(ld.dims) and len(ri) == len(rd.dims):
                        self.uf.union(ld.hier + keystr(li), rd.hier + keystr(ri))
                        continue
                    if li is None and not ld.dims and ri is not None and len(ri) == len(rd.dims) \
                            and ld.width == rd.width and ld.signed == rd.signed:
                        self.uf.union(ld.hier, rd.hier + keystr(ri))
                        continue
                    if ri is None and not rd.dims and li is not None and len(li) == len(ld.dims) \
                            and ld.width == rd.width and ld.signed == rd.signed:
                        self.uf.union(ld.hier + keystr(li), rd.hier)
                        continue
                self.comb.append((lv, rhs, 0))
            elif kind == "always_ff":
                clk = scope.lookup(it[1])
                rst = scope.lookup(it[2]) if it[2] else None
                self.ff_blocks.append((clk, rst, self._resolve_stmt(it[3], scope)))
            elif kind == "always_comb":
                self.comb_blocks.append(self._resolve_stmt(it[1], scope))
            elif kind == "genfor":
                _, var, init, cond, step, label, body = it
                v = self._const(init, scope)
                n = 0
                while True:
                    s = Scope(scope, scope.prefix)
                    s.names[var] = v
                    if self._const(cond, s) == 0:
                        break
                    inner = Scope(scope, f"{scope.prefix}.{label or 'genblk'}[{v}]")
                    inner.names[var] = v
                    self._elab_items(body, inner)
                    v = self._const(step, s)
                    n += 1
                    if n > 100000:
                        raise RuntimeError("generate loop did not terminate")
            elif kind == "genif":
                _, cond, l1, items1, l2, items2 = it
                if self._const(cond, scope):
                    self._elab_items(items1, Scope(scope, f"{scope.prefix}.{l1}") if l1 else scope)
                elif items2:
                    self._elab_items(items2, Scope(scope, f"{scope.prefix}.{l2}") if l2 else scope)
            elif kind == "inst":
                _, modname, pexprs, instname, conns = it
                params = {p: self._const(e, scope) for p, e in pexprs}
                rconns = [(p, self._resolve(e, scope) if e is not None else None) for p, e in conns]
                self._elab_module(modname, params, f"{scope.prefix}.{instname}", rconns)
            else:
                raise ValueError(f"unknown item {kind}")

    # -- identifier resolution: ('ident', n) -> ('const', v) | ('sig', decl, idx_tuple_of_exprs) --
    def _resolve(self, e, scope: Scope):
        k = e[0]
        if k == "ident":
            v = scope.lookup(e[1])
            if isinstance(v, int):
                return ("const", v)
            if isinstance(v, tuple) and v[0] == "loopvar":
                return ("loopvar", v[1])
            return ("sig", v, ())
        if k == "index":
            base = self._resolve(e[1], scope)
            idx = self._resolve(e[2], scope)
            if base[0] != "sig":
                raise ValueError("indexing a non-signal")
            return ("sig", base[1], base[2] + (idx,))
        if k == "part":
            return ("part", self._resolve(e[1], scope), self._resolve(e[2], scope), self._resolve(e[3], scope))
        if k == "partidx":
            return ("partidx", self._resolve(e[1], scope), self._resolve(e[2], scope), self._const(e[3], scope), e[4])
        if k == "reduce":
            return ("reduce", e[1], self._resolve(e[2], scope))
        if k == "num":
            return ("num", e[1], e[2], e[3])
        if k == "fill":
            return e
        if k == "binop":
            return ("binop", e[1], self._resolve(e[2], scope), self._resolve(e[3], scope))
        if k == "unop":
            return ("unop", e[1], self._resolve(e[2], scope))
        if k == "tern":
            return ("tern", self._resolve(e[1], scope), self._resolve(e[2], scope), self._resolve(e[3], scope))
        if k == "cast":
            return ("cast", self._const(e[1], scope), self._resolve(e[2], scope))
        if k == "concat":
            return ("concat", [self._resolve(x, scope) for x in e[1]])
        if k == "repl":
            return ("repl", self._const(e[1], scope), self._resolve(e[2], scope))
        if k == "call":
            return ("call", e[1], [self._resolve(x, scope) for x in e[2]])
        raise ValueError(f"unknown expr {k}")

    def _resolve_stmt(self, s, scope: Scope):
        k = s[0]
        if k == "block":
            inner = Scope(scope, scope.prefix)
            return ("block", [self._resolve_stmt(x, inner) for x in s[1]])
        if k == "local":
            for n in s[1]:
                scope.names[n] = ("loopvar", n)
            return ("local", s[1])
        if k == "if":
            return ("if", self._resolve(s[1], scope), self._resolve_stmt(s[2], scope),
                    self._resolve_stmt(s[3], scope) if s[3] else None)
        if k == "for":
            _, var, init, cond, sv, sop, sexpr, body = s
            inner = Scope(scope, scope.prefix)
            inner.names[var] = ("loopvar", var)
            return ("for", var, self._resolve(init, scope), self._resolve(cond, inner), sv, sop,
                    self._resolve(sexpr, inner), self._resolve_stmt(body, inner))
        if k == "assign":
            return ("assign", self._resolve(s[1], scope), self._resolve(s[2], scope), s[3])
        if k == "case":
            return ("case", self._resolve(s[1], scope),
                    [([self._resolve(l, scope) for l in labels], self._resolve_stmt(st, scope)) for labels, st in s[2]],
                    self._resolve_stmt(s[3], scope) if s[3] else None)
        raise ValueError(f"unknown stmt {k}")

    # -- cells --
    def _build_cells(self):
        self.cells: Dict[str, Cell] = {}
        roots: Dict[str, Cell] = {}
        for hier, d in self.sigs.items():
            for key in element_keys(d):
                full = hier + key
                r = self.uf.find(full)
                c = roots.get(r)
                if c is None:
                    c = roots[r] = Cell(d.width, d.signed, full)
                elif c.w != d.width:
                    raise ValueError(f"aliased signals differ in width: {full} vs {c.name}")
                self.cells[full] = c
        # compile processes
        self.comb_nodes: List[Callable[[], bool]] = []
        for lv, rhs, _ in self.comb:
            self.comb_nodes.append(self._compile_assign(lv, rhs))
        for st in self.comb_blocks:
            fn = self._compile_stmt(st, blocking=True)
            written = []
            for d in stmt_targets(st):
                written.extend(self.cells[d.hier + key] for key in element_keys(d))

            def node(fn=fn, written=written):
                before = [c.v for c in written]
                fn({}, None)
                return any(c.v != b for c, b in zip(written, before))
            self.comb_nodes.append(node)
        self.ff_nodes = []
        for clk, rst, st in self.ff_blocks:
            self.ff_nodes.append(self._compile_stmt(st, blocking=False))

    def cell_of(self, hier_or_port: str, *idx) -> Cell:
        hier = self.top_ports.get(hier_or_port, hier_or_port)
        return self.cells[hier + (keystr(idx) if idx else "")]

    def array_of(self, port: str):
        d = self.sigs[self.top_ports.get(port, port)]
        return [self.cell_of(port, i) for i in range(d.dims[0][1])]

    # -- compilation to closures --
    def _target(self, lv):
        """Returns fn(env) -> Cell for a resolved lvalue (whole cell); part-selects use _target_slice."""
        if lv[0] in ("part", "partidx"):
            raise ValueError("part-select lvalue needs _target_slice")
        _, d, idx = lv
        if len(idx) != len(d.dims):
            raise ValueError(f"lvalue {d.hier} needs {len(d.dims)} indices")
        idx_fns = [self._compile_expr(i, True)[0] for i in idx]
        consts = [const_fold(i) for i in idx]
        if all(c is not None for c in consts):
            cell = self.cells[d.hier + keystr(consts)]
            return lambda env: cell
        los = [lo for lo, _ in d.dims]
        hier, cells = d.hier, self.cells

        def fn(env):
            return cells[hier + keystr([f(env) for f in idx_fns])]
        return fn

    def _target_slice(self, lv):
        """Resolved part-select lvalue -> (cell_fn, lo_fn, width)."""
        if lv[0] == "partidx":
            _, base, idx, w, d = lv
            cfn = self._target(base)
            ifn, _ = self._compile_expr(idx, True)
            if d == "+":
                return cfn, (lambda env: ifn(env)), w
            return cfn, (lambda env: ifn(env) - w + 1), w
        _, base, msb, lsb = lv
        cfn = self._target(base)
        cm, cl = const_fold(msb), const_fold(lsb)
        if cm is not None and cl is not None:
            lo, w = min(cm, cl), abs(cm - cl) + 1
            return cfn, (lambda env: lo), w
        wc = const_fold(("binop", "-", msb, lsb))
        if wc is None:
            raise ValueError("part-select lvalue width must be constant")
        w = abs(wc) + 1
        mfn, _ = self._compile_expr(msb, True)
        lfn, _ = self._compile_expr(lsb, True)
        return cfn, (lambda env: min(mfn(env), lfn(env))), w

    @staticmethod
    def _bitsel_to_slice(lv):
        """A trailing index beyond the unpacked dims is a bit-select: rewrite as a 1-bit slice."""
        if lv[0] == "sig" and len(lv[2]) == len(lv[1].dims) + 1:
            return ("partidx", ("sig", lv[1], lv[2][:-1]), lv[2][-1], 1, "+")
        return lv

    def _compile_assign(self, lv, rhs):
        lv = self._bitsel_to_slice(lv)
        if lv[0] in ("part", "partidx"):
            cfn, lofn, w = self._target_slice(lv)
            rfn, _ = self._compile_expr(rhs, is_signed(rhs), ctx_width=w)
            mask = (1 << w) - 1

            def node():
                c = cfn({})
                lo = lofn({})
                nv = (c.v & ~(mask << lo)) | ((rfn({}) & mask) << lo)
                if c.v != nv:
                    c.v = nv
                    return True
                return False
            return node
        tgt = self._target(lv)
        w = lv[1].width
        signed_ctx = is_signed(rhs)
        rfn, _ = self._compile_expr(rhs, signed_ctx, ctx_width=w)
        mask = (1 << w) - 1

        def node():
            c = tgt({})
            nv = rfn({}) & mask
            if c.v != nv:
                c.v = nv
                return True
            return False
        return node

    def _compile_stmt(self, s, blocking: bool):
        k = s[0]
        if k == "block":
            fns = [self._compile_stmt(x, blocking) for x in s[1]]

            def run(env, pending):
                for f in fns:
                    f(env, pending)
            return run
        if k == "if":
            cfn, _ = self._compile_expr(s[1], False)
            tfn = self._compile_stmt(s[2], blocking)
            efn = self._compile_stmt(s[3], blocking) if s[3] else None

            def run(env, pending):
                if cfn(env):
                    tfn(env, pending)
                elif efn:
                    efn(env, pending)
            return run
        if k == "for":
            _, var, init, cond, sv, sop, sexpr, body = s
            ifn, _ = self._compile_expr(init, True)
            cfn, _ = self._compile_expr(cond, False)
            sfn, _ = self._compile_expr(sexpr, True)
            bfn = self._compile_stmt(body, blocking)

            def run(env, pending):
                env = dict(env)
                env[var] = ifn(env)
                while cfn(env):
                    bfn(env, pending)
                    if sop == "+":
                        env[var] += sfn(env)
                    elif sop == "-":
                        env[var] -= sfn(env)
                    else:
                        env[var] = sfn(env)
            return run
        if k == "local":
            names = s[1]

            def run(env, pending):
                for n in names:
                    env[n] = 0
            return run
        if k == "case":
            _, sel, arms, default = s
            sfn, _ = self._compile_expr(sel, False)
            carms = []
            for labels, st in arms:
                lfns = [self._compile_expr(l, False)[0] for l in labels]
                carms.append((lfns, self._compile_stmt(st, blocking)))
            dfn = self._compile_stmt(default, blocking) if default else None

            def run(env, pending):
                v = sfn(env)
                for lfns, st in carms:
                    if any(f(env) == v for f in lfns):
                        st(env, pending)
                        return
                if dfn:
                    dfn(env, pending)
            return run
        if k == "assign" and self._bitsel_to_slice(s[1])[0] in ("part", "partidx"):
            _, lv, rhs, blk = s
            lv = self._bitsel_to_slice(lv)
            cfn, lofn, w = self._target_slice(lv)
            rfn, _ = self._compile_expr(rhs, is_signed(rhs), ctx_width=w)
            mask = (1 << w) - 1

            def run(env, pending):
                c = cfn(env)
                lo = lofn(env)
                v = rfn(env) & mask
                if blocking or pending is None:
                    c.v = (c.v & ~(mask << lo)) | (v << lo)
                else:
                    pending.append((c, (lo, mask, v)))
            return run
        if k == "assign":
            _, lv, rhs, blk = s
            if lv[0] == "loopvar":
                name = lv[1]
                rfn, _ = self._compile_expr(rhs, True, 32)

                def run(env, pending):
                    env[name] = rfn(env)
                return run
            tgt = self._target(lv)
            w = lv[1].width
            rfn, _ = self._compile_expr(rhs, is_signed(rhs), ctx_width=w)
            mask = (1 << w) - 1

            def run(env, pending):
                c = tgt(env)
                v = rfn(env) & mask
                if blocking or pending is None:
                    c.v = v
                else:
                    pending.append((c, v))
            return run
        raise ValueError(k)

    def _compile_expr(self, e, signed_ctx: bool, ctx_width: int = 32):
        """Returns (fn(env)->int, width). Values are Python ints: signed interpretation when
        signed_ctx and the leaf is signed, else the unsigned bit pattern."""
        k = e[0]
        if k == "const":
            v = e[1]
            return (lambda env: v), 32
        if k == "num":
            _, v, w, s = e
            if s and signed_ctx and w < 64 and v >= (1 << (w - 1)):
                v -= (1 << w)
            return (lambda env: v), w
        if k == "fill":
            v = 0 if e[1] == 0 else (1 << ctx_width) - 1
            return (lambda env: v), ctx_width
        if k == "sig":
            _, d, idx = e
            if len(idx) == len(d.dims) + 1:        # trailing bit-select on the packed vector
                bfn, _ = self._compile_expr(("sig", d, idx[:-1]), False)
                ifn, _ = self._compile_expr(idx[-1], False)
                return (lambda env: (bfn(env) >> ifn(env)) & 1), 1
            if len(idx) != len(d.dims):
                raise ValueError(f"signal {d.hier} used with {len(idx)} of {len(d.dims)} indices")
            consts = [const_fold(i) for i in idx]
            if all(c is not None for c in consts):
                cell = self.cells[d.hier + keystr(consts)]
                if d.signed and signed_ctx:
                    half, full = 1 << (d.width - 1), 1 << d.width
                    return (lambda env: cell.v - full if cell.v >= half else cell.v), d.width
                return (lambda env: cell.v), d.width
            idx_fns = [self._compile_expr(i, True)[0] for i in idx]
            hier, cells = d.hier, self.cells
            if d.signed and signed_ctx:
                half, full = 1 << (d.width - 1), 1 << d.width

                def fn(env):
                    v = cells[hier + keystr([f(env) for f in idx_fns])].v
                    return v - full if v >= half else v
                return fn, d.width
            return (lambda env: cells[hier + keystr([f(env) for f in idx_fns])].v), d.width
        if k == "part":
            _, base, msb, lsb = e
            bfn, _ = self._compile_expr(base, False)
            cm, cl = const_fold(msb), const_fold(lsb)
            if cm is not None and cl is not None:
                lo, hi = min(cm, cl), max(cm, cl)
                w = hi - lo + 1
                mask = (1 << w) - 1
                return (lambda env: (bfn(env) >> lo) & mask), w
            wc = const_fold(("binop", "-", msb, lsb))
            if wc is None:
                raise ValueError("part-select width must be constant")
            w = abs(wc) + 1
            mask = (1 << w) - 1
            mfn, _ = self._compile_expr(msb, True)
            lfn, _ = self._compile_expr(lsb, True)
            return (lambda env: (bfn(env) >> min(mfn(env), lfn(env))) & mask), w
        if k == "partidx":
            _, base, idx, w, d = e
            bfn, _ = self._compile_expr(base, False)
            ifn, _ = self._compile_expr(idx, True)
            mask = (1 << w) - 1
            if d == "+":
                return (lambda env: (bfn(env) >> ifn(env)) & mask), w
            return (lambda env: (bfn(env) >> (ifn(env) - w + 1)) & mask), w
        if k == "reduce":
            op = e[1]
            a, w = self._compile_expr(e[2], False)
            mask = (1 << w) - 1
            if op == "^":
                return (lambda env: bin(a(env) & mask).count("1") & 1), 1
            if op == "&":
                return (lambda env: int((a(env) & mask) == mask)), 1
            return (lambda env: int((a(env) & mask) != 0)), 1
        if k == "loopvar":
            name = e[1]
            return (lambda env: env[name]), 32
        if k == "binop":
            op = e[1]
            if op in ("==", "!=", "<", "<=", ">", ">="):
                sc = is_signed(e[2]) and is_signed(e[3])
                a, wa = self._compile_expr(e[2], sc)
                b, wb = self._compile_expr(e[3], sc)
                f = {"==": lambda x, y: int(x == y), "!=": lambda x, y: int(x != y),
                     "<": lambda x, y: int(x < y), "<=": lambda x, y: int(x <= y),
                     ">": lambda x, y: int(x > y), ">=": lambda x, y: int(x >= y)}[op]
                return (lambda env: f(a(env), b(env))), 1
            if op in ("&&", "||"):
                a, _ = self._compile_expr(e[2], False)
                b, _ = self._compile_expr(e[3], False)
                if op == "&&":
                    return (lambda env: int(bool(a(env)) and bool(b(env)))), 1
                return (lambda env: int(bool(a(env)) or bool(b(env)))), 1
            a, wa = self._compile_expr(e[2], signed_ctx, ctx_width)
            b, wb = self._compile_expr(e[3], signed_ctx, ctx_width)
            w = max(wa, wb)
            f = {"+": lambda x, y: x + y, "-": lambda x, y: x - y, "*": lambda x, y: x * y,
                 "/": lambda x, y: int(x / y) if y else 0, "%": lambda x, y: (abs(x) % abs(y)) * (1 if x >= 0 else -1) if y else 0,
                 "&": lambda x, y: x & y, "|": lambda x, y: x | y, "^": lambda x, y: x ^ y,
                 "<<": lambda x, y: x << y, ">>": lambda x, y: x >> y,
                 "<<<": lambda x, y: x << y, ">>>": lambda x, y: x >> y}[op]
            return (lambda env: f(a(env), b(env))), w
        if k == "unop":
            op = e[1]
            if op == "!":
                a, _ = self._compile_expr(e[2], False)
                return (lambda env: int(not a(env))), 1
            if op == "-":
                a, w = self._compile_expr(e[2], signed_ctx, ctx_width)
                return (lambda env: -a(env)), w
            if op == "~":
                a, w = self._compile_expr(e[2], signed_ctx, ctx_width)
                mask = (1 << max(w, ctx_width)) - 1
                return (lambda env: (~a(env)) & mask), w
            raise ValueError(op)
        if k == "tern":
            c, _ = self._compile_expr(e[1], False)
            a, wa = self._compile_expr(e[2], signed_ctx, ctx_width)
            b, wb = self._compile_expr(e[3], signed_ctx, ctx_width)
            return (lambda env: a(env) if c(env) else b(env)), max(wa, wb)
        if k == "cast":
            w = e[1]
            a, _ = self._compile_expr(e[2], signed_ctx, w)
            mask = (1 << w) - 1
            if signed_ctx and is_signed(e[2]):
                half, full = 1 << (w - 1), 1 << w
                return (lambda env: ((a(env) & mask) - full) if (a(env) & mask) >= half else (a(env) & mask)), w
            return (lambda env: a(env) & mask), w
        if k == "concat":
            parts = [self._compile_expr(x, False) for x in e[1]]
            total = sum(w for _, w in parts)

            def fn(env):
                v = 0
                for f, w in parts:
                    v = (v << w) | (f(env) & ((1 << w) - 1))
                return v
            return fn, total
        if k == "repl":
            n = e[1]
            f, w = self._compile_expr(e[2], False)
            mask = (1 << w) - 1

            def fn(env):
                x = f(env) & mask
                v = 0
                for _ in range(n):
                    v = (v << w) | x
                return v
            return fn, w * n
        if k == "call":
            name, args = e[1], e[2]
            if name == "$clog2":
                a, _ = self._compile_expr(args[0], False)
                return (lambda env: clog2(a(env))), 32
            if name == "$signed":
                a, w = self._compile_expr(args[0], True, ctx_width)
                return a, w
            if name == "$unsigned":
                a, w = self._compile_expr(args[0], False, ctx_width)
                return a, w
            raise ValueError(f"unknown function {name}")
        raise ValueError(f"cannot compile {k}")

    # -- simulation --
    def settle(self, max_iter=200):
        for _ in range(max_iter):
            changed = False
            for n in self.comb_nodes:
                if n():
                    changed = True
            if not changed:
                return
        raise RuntimeError("combinational logic did not settle (loop?)")

    def tick(self):
        """One clock: settle combinational logic, evaluate every always_ff, commit, settle."""
        self.settle()
        pending: List[Tuple[Cell, Any]] = []
        for f in self.ff_nodes:
            f({}, pending)
        for c, v in pending:
            if isinstance(v, tuple):
                lo, mask, val = v
                c.v = (c.v & ~(mask << lo)) | (val << lo)
            else:
                c.v = v
        self.settle()


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def clog2(x: int) -> int:
    return 0 if x <= 1 else (x - 1).bit_length()


def keystr(idx) -> str:
    idx = list(idx)
    return "" if not idx else "[" + ",".join(str(int(i)) for i in idx) + "]"


def element_keys(d: SigDecl):
    if not d.dims:
        return [""]
    keys = [[]]
    for lo, size in d.dims:
        keys = [k + [lo + i] for k in keys for i in range(size)]
    return [keystr(k) for k in keys]


def stmt_targets(s, out=None):
    """Signal declarations written anywhere inside a (resolved) statement."""
    if out is None:
        out = []
    k = s[0]
    if k == "block":
        for x in s[1]:
            stmt_targets(x, out)
    elif k == "if":
        stmt_targets(s[2], out)
        if s[3]:
            stmt_targets(s[3], out)
    elif k == "for":
        stmt_targets(s[7], out)
    elif k == "assign":
        lv = s[1]
        if lv[0] in ("part", "partidx"):
            lv = lv[1]
        if lv[0] == "sig":
            d = lv[1]
            if d not in out:
                out.append(d)
    elif k == "case":
        for _, st in s[2]:
            stmt_targets(st, out)
        if s[3]:
            stmt_targets(s[3], out)
    return out


def as_ref(e):
    """('sig', decl, idx) with constant indices -> (decl, tuple_of_ints or None)."""
    if e[0] != "sig":
        return None
    d, idx = e[1], e[2]
    if not idx:
        return (d, None)
    consts = [const_fold(i) for i in idx]
    if any(c is None for c in consts):
        return None
    return (d, tuple(consts))


def const_fold(e) -> Optional[int]:
    k = e[0]
    if k == "const":
        return e[1]
    if k == "num":
        _, v, w, s = e
        if s and v >= (1 << (w - 1)) and w < 64:
            v -= 1 << w
        return v
    if k == "binop":
        a, b = const_fold(e[2]), const_fold(e[3])
        if a is None or b is None:
            return None
        op = e[1]
        if op == "+": return a + b
        if op == "-": return a - b
        if op == "*": return a * b
        if op == "/": return int(a / b) if b else 0
        if op == "%": return a % b if b else 0
        if op == "==": return int(a == b)
        if op == "!=": return int(a != b)
        if op == "<": return int(a < b)
        if op == "<=": return int(a <= b)
        if op == ">": return int(a > b)
        if op == ">=": return int(a >= b)
        if op == "&&": return int(bool(a) and bool(b))
        if op == "||": return int(bool(a) or bool(b))
        if op == "&": return a & b
        if op == "|": return a | b
        if op == "^": return a ^ b
        if op in ("<<", "<<<"): return a << b if b >= 0 else a >> -b
        if op in (">>", ">>>"): return a >> b if b >= 0 else a << -b
        raise ValueError(op)
    if k == "unop":
        a = const_fold(e[2])
        if a is None:
            return None
        return {"-": -a, "!": int(not a), "~": ~a}[e[1]]
    if k == "tern":
        c = const_fold(e[1])
        if c is None:
            return None
        return const_fold(e[2]) if c else const_fold(e[3])
    if k == "call" and e[1] == "$clog2":
        a = const_fold(e[2][0])
        return None if a is None else clog2(a)
    if k == "cast":
        a = const_fold(e[2])
        return None if a is None else a & ((1 << e[1]) - 1)
    return None


def is_signed(e) -> bool:
    k = e[0]
    if k == "sig":
        return e[1].signed
    if k in ("num",):
        return e[3]
    if k == "const":
        return True
    if k == "loopvar":
        return True
    if k == "fill":
        return False
    if k == "binop":
        if e[1] in ("==", "!=", "<", "<=", ">", ">=", "&&", "||"):
            return False
        return is_signed(e[2]) and is_signed(e[3])
    if k == "unop":
        return False if e[1] == "!" else is_signed(e[2])
    if k == "tern":
        return is_signed(e[2]) and is_signed(e[3])
    if k == "cast":
        return is_signed(e[2])
    if k in ("concat", "repl", "part", "partidx", "reduce"):
        return False
    if k == "call":
        return {"$signed": True, "$unsigned": False}.get(e[1], True)
    return False


def load(files: List[str]) -> Dict[str, ModuleDef]:
    mods = {}
    for f in files:
        with open(f) as fh:
            for m in Parser(lex(fh.read(), f), f).parse_file():
                mods[m.name] = m
    return mods


if __name__ == "__main__":
    mods = load(sys.argv[1:])
    print("parsed modules:", ", ".join(mods))
