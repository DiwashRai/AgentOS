// TypeScript measurements for tools/gates/gates.py, with the TypeScript compiler API.
//
// Usage: node ts_measures.mjs <web dir> <file>...   (files relative to the web dir)
// Prints one JSON object: {"<file>": {code_lines, suppressions, raw_tags, functions}}.
//   code_lines    lines with code (not blank, not only comments)
//   suppressions  [[line, marker]] for eslint-disable*, @ts-ignore, @ts-expect-error comments
//   raw_tags      [line] of each JSX intrinsic element (<div>, <span>, ...)
//   functions     [{name, start: [line, col], end: [line, col]}], 1-based, to name ESLint findings
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

const [webDir, ...files] = process.argv.slice(2);
const ts = createRequire(join(webDir, "package.json"))("typescript");
const MARKERS = /eslint-disable(?:-next-line|-line)?|@ts-ignore|@ts-expect-error/g;

function functionName(node) {
  if (node.name && ts.isIdentifier(node.name)) return node.name.text;
  if (node.name) return node.name.getText();
  if (ts.isConstructorDeclaration(node)) return "constructor";
  const parent = node.parent;
  if (ts.isVariableDeclaration(parent) || ts.isPropertyAssignment(parent)) {
    return parent.name.getText();
  }
  if (ts.isCallExpression(parent)) return `${parent.expression.getText()}(callback)`;
  if (ts.isJsxExpression(parent)) return `${parent.parent.name?.getText() ?? "jsx"}(callback)`;
  return "<anonymous>";
}

function measure(path) {
  const text = readFileSync(join(webDir, path), "utf8");
  const kind = path.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, kind);
  const at = (pos) => {
    const { line, character } = sf.getLineAndCharacterOfPosition(pos);
    return [line + 1, character + 1];
  };
  const comments = new Map(); // start -> end
  const jsxText = []; // [start, end]: "//" there is text, not a comment
  const rawTags = [];
  const functions = [];

  const visit = (node, scope) => {
    if (ts.isJSDoc(node)) return; // inside a comment
    if (node.kind === ts.SyntaxKind.JsxText) {
      jsxText.push([node.pos, node.end]);
      return;
    }
    const ranges = [
      ...(ts.getLeadingCommentRanges(text, node.pos) ?? []),
      ...(ts.getTrailingCommentRanges(text, node.end) ?? []),
    ];
    for (const range of ranges) comments.set(range.pos, range.end);
    let inner = scope;
    if (ts.isClassLike(node) && node.name) inner = scope ? `${scope}.${node.name.text}` : node.name.text;
    if (ts.isFunctionLike(node) && "body" in node && node.body) {
      inner = scope ? `${scope}.${functionName(node)}` : functionName(node);
      functions.push({ name: inner, start: at(node.getStart(sf)), end: at(node.end) });
    }
    if (
      (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) &&
      ts.isIdentifier(node.tagName) &&
      ts.isIntrinsicJsxName(node.tagName.text)
    ) {
      rawTags.push(at(node.getStart(sf))[0]);
    }
    for (const child of node.getChildren(sf)) visit(child, inner);
  };
  visit(sf, "");

  const commentMask = new Uint8Array(text.length);
  const suppressions = [];
  for (const [start, end] of comments) {
    if (jsxText.some(([from, to]) => start >= from && start < to)) continue;
    commentMask.fill(1, start, end);
    for (const match of text.slice(start, end).matchAll(MARKERS)) {
      suppressions.push([at(start + match.index)[0], match[0]]);
    }
  }
  let codeLines = 0;
  let hasCode = false;
  for (let i = 0; i <= text.length; i++) {
    if (i === text.length || text[i] === "\n") {
      if (hasCode) codeLines++;
      hasCode = false;
    } else if (!commentMask[i] && !/\s/.test(text[i])) {
      hasCode = true;
    }
  }
  return { code_lines: codeLines, suppressions, raw_tags: rawTags, functions };
}

const out = {};
for (const path of files) out[path] = measure(path);
process.stdout.write(JSON.stringify(out));
