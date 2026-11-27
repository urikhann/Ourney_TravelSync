const test = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

test('agent Markdown renders headings, emphasis and lists without clipping long replies', async () => {
  const { default: Markdown } = await import('react-markdown');
  const reply = '## Planning rules\n\n' + '- **Group fit:** preferences\n'.repeat(180) + '\nComplete explanation.';
  const html = renderToStaticMarkup(React.createElement(Markdown, { skipHtml: true }, reply));
  assert.ok(reply.length > 2000);
  assert.ok(html.includes('<h2>Planning rules</h2>'));
  assert.ok(html.includes('<strong>Group fit:</strong>'));
  assert.ok(html.includes('<ul>'));
  assert.ok(html.includes('Complete explanation.'));
});

test('agent Markdown does not execute HTML or unsafe links', async () => {
  const { default: Markdown } = await import('react-markdown');
  const reply = '<script>alert(1)</script>\n\n[unsafe](javascript:alert(1))';
  const html = renderToStaticMarkup(React.createElement(Markdown, { skipHtml: true }, reply));
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('javascript:'));
});
