const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const root = path.join(__dirname, '../src');
const compile = file => ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
}).outputText;
const dictionary = {};
new Function('exports', compile('i18n.ko.ts'))(dictionary);
const { KO } = dictionary;
const parse = file => ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true);
const walk = (node, visit) => { visit(node); ts.forEachChild(node, child => walk(child, visit)); };
const files = dir => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => e.isDirectory()
  ? files(path.join(dir, e.name)) : /\.tsx?$/.test(e.name) ? [path.join(dir, e.name)] : []);
function literalOptions(node) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return [node.text];
  if (ts.isConditionalExpression(node)) return [...literalOptions(node.whenTrue), ...literalOptions(node.whenFalse)];
  if (ts.isBinaryExpression(node) && [ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(node.operatorToken.kind))
    return [...literalOptions(node.left), ...literalOptions(node.right)];
  return [];
}
function i18n(language) {
  const result = {};
  const requireStub = name => name === './i18n.ko' ? dictionary : name === 'react' ? { createContext: () => ({}) } : {};
  new Function('require', 'exports', 'localStorage', 'navigator', compile('i18n.tsx'))(
    requireStub, result, { getItem: () => language }, { language });
  return result;
}

test('every fixed UI translation and error message has a Korean entry', () => {
  const missing = [];
  const check = (text, file) => {
    if (/[A-Za-z]/.test(text) && !Object.hasOwn(KO, text)) missing.push(`${path.relative(root, file)}: ${text}`);
  };
  for (const file of files(root).filter(f => !f.endsWith('i18n.ko.ts'))) {
    walk(parse(file), node => {
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && ['t', 'tr'].includes(node.expression.text) && node.arguments.length)
        literalOptions(node.arguments[0]).forEach(text => check(text, file));
      if (ts.isVariableDeclaration(node) && ['MESSAGES', 'SOURCE', 'CLOSED', 'scopeLabels'].includes(node.name.getText())
          && node.initializer && ts.isObjectLiteralExpression(node.initializer)) {
        for (const property of node.initializer.properties)
          if (ts.isPropertyAssignment(property) && ts.isStringLiteral(property.initializer)) check(property.initializer.text, file);
      }
      if (ts.isVariableDeclaration(node) && node.name.getText() === 'QUICK' && node.initializer)
        walk(node.initializer, item => {
          if (ts.isPropertyAssignment(item) && ['label', 'text'].includes(item.name.getText()) && ts.isStringLiteral(item.initializer))
            check(item.initializer.text, file);
        });
    });
  }
  assert.deepEqual(missing, []);
});

test('Korean translations preserve placeholders and have no duplicate keys', () => {
  const placeholders = text => [...new Set(text.match(/\{\w+\}/g) || [])].sort();
  const keys = new Set();
  walk(parse(path.join(root, 'i18n.ko.ts')), node => {
    if (!ts.isPropertyAssignment(node) || !ts.isStringLiteral(node.name) || !ts.isStringLiteral(node.initializer)) return;
    assert.ok(!keys.has(node.name.text), `Duplicate translation: ${node.name.text}`);
    keys.add(node.name.text);
    assert.deepEqual(placeholders(node.initializer.text), placeholders(node.name.text), node.name.text);
    assert.ok(node.initializer.text.trim(), `Empty translation: ${node.name.text}`);
  });
});

test('recent UI labels, errors and recommendation reasons render in Korean', () => {
  const { t } = i18n('ko');
  assert.equal(t('{n} stops', { n: 3 }), '방문지 3곳');
  assert.equal(t('Choose Plan {x}', { x: 'A' }), '일정 A 선택');
  for (const key of ['Choose a date', 'Suggested change',
    'The optimizer returned an invalid response. Your saved plan is kept.',
    'Structured preferences and semantic relevance guide the ranking; fallback is recorded per member.',
    'Feature preferences guide this ranking because semantic scoring was unavailable or neutral.'])
    assert.match(t(key), /[가-힣]/);
});

test('known system messages follow the selected language while source text is preserved', () => {
  const en = i18n('en'), ko = i18n('ko');
  const question = 'Which date should I change? Choose a day below.';
  assert.equal(en.systemText(ko.systemText(question)), question);
  assert.equal(ko.systemText(question), KO[question]);
  assert.equal(ko.systemText('A member wrote this custom sentence.'), 'A member wrote this custom sentence.');
  assert.equal(en.systemText('고유한 장소 설명'), '고유한 장소 설명');
});
