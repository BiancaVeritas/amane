/**
 * Jinja2 模板的高亮切分.
 *
 * 只认三种标记: 表达式 ``{{ }}``、语句 ``{% %}``、注释 ``{# #}``; 其余都是原样文本.
 * 不解析表达式内部语法 —— 多行框里再叠一层语法树没有收益, 标出边界与错误已足够.
 */

export const JINJA_TOKEN_KINDS = [
  "text",
  "delimiter",
  "variable",
  "keyword",
  "string",
  "number",
  "operator",
  "error",
] as const;

export type JinjaTokenKind = (typeof JINJA_TOKEN_KINDS)[number];

export interface JinjaToken {
  kind: JinjaTokenKind;
  text: string;
}

/** 语句关键字. 只列模板里真正会写的那些. */
const KEYWORDS = new Set([
  "if",
  "elif",
  "else",
  "endif",
  "for",
  "endfor",
  "set",
  "in",
  "not",
  "and",
  "or",
  "is",
]);

const DELIMITERS = ["{{", "}}", "{%", "%}", "{#", "#}"] as const;

/**
 * 把一段 ``{{...}}`` / ``{%...%}`` / ``{#...}`` 内部切成词.
 *
 * 字符串整体算一个 token (内部还可能包含 ``}}``, 不能被当成分隔符);
 * 其余按关键字 / 数字 / 运算符 / 变量粗分.
 */
function tokenizeInner(src: string, out: JinjaToken[]): void {
  let i = 0;
  let buf = "";
  const flush = (kind: JinjaTokenKind): void => {
    if (buf) {
      out.push({ kind, text: buf });
      buf = "";
    }
  };

  while (i < src.length) {
    const ch = src[i];

    // 字符串: 一直吃到配对引号
    if (ch === "'" || ch === '"') {
      flush("variable");
      let j = i + 1;
      while (j < src.length && src[j] !== ch) {
        j += src[j] === "\\" ? 2 : 1;
      }
      const end = Math.min(j + 1, src.length);
      out.push({ kind: "string", text: src.slice(i, end) });
      i = end;
      continue;
    }

    // 标识符 / 关键字
    if (/[A-Za-z_]/.test(ch)) {
      flush("variable");
      let j = i;
      while (j < src.length && /[A-Za-z0-9_]/.test(src[j])) {
        j += 1;
      }
      const word = src.slice(i, j);
      out.push({ kind: KEYWORDS.has(word) ? "keyword" : "variable", text: word });
      i = j;
      continue;
    }

    // 数字
    if (/[0-9]/.test(ch)) {
      flush("variable");
      let j = i;
      while (j < src.length && /[0-9.]/.test(src[j])) {
        j += 1;
      }
      out.push({ kind: "number", text: src.slice(i, j) });
      i = j;
      continue;
    }

    // 运算符与标点
    if (/[=<>!|+\-*/%~.,:()[\]{}]/.test(ch)) {
      flush("variable");
      out.push({ kind: "operator", text: ch });
      i += 1;
      continue;
    }

    buf += ch;
    i += 1;
  }
  flush("variable");
  // 空白也必须逐个保留: 高亮层要逐字符对齐 textarea, 少一个空格后面全部错位.
  // (曾经在这里删过纯空白 token, 结果 ``{% if def %}`` 被渲染成 ``{%ifdef%}``.)
}

/** 把整段模板切成 token; 未闭合的标记标 ``error``. */
export function tokenizeJinja(src: string): JinjaToken[] {
  const tokens: JinjaToken[] = [];
  let i = 0;
  let text = "";

  const flushText = (): void => {
    if (text) {
      tokens.push({ kind: "text", text });
      text = "";
    }
  };

  while (i < src.length) {
    const open = DELIMITERS.find((d) => d.length === 2 && src.startsWith(d, i));
    if (!open) {
      text += src[i];
      i += 1;
      continue;
    }

    flushText();
    tokens.push({ kind: "delimiter", text: open });
    i += 2;

    if (open === "{#") {
      const end = src.indexOf("#}", i);
      if (end === -1) {
        tokens.push({ kind: "error", text: src.slice(i) });
        return tokens;
      }
      tokens.push({ kind: "text", text: src.slice(i, end) });
      tokens.push({ kind: "delimiter", text: "#}" });
      i = end + 2;
      continue;
    }

    const close = open === "{{" ? "}}" : "%}";
    const end = src.indexOf(close, i);
    if (end === -1) {
      tokens.push({ kind: "error", text: src.slice(i) });
      return tokens;
    }
    tokenizeInner(src.slice(i, end), tokens);
    tokens.push({ kind: "delimiter", text: close });
    i = end + 2;
  }

  flushText();
  return tokens;
}
