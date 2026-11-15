import assert from 'node:assert/strict'
import test from 'node:test'
import { translateDemoText } from '../englishDom'

test('demo translation covers Agent controls and dynamic failure text', () => {
  assert.equal(translateDemoText('暂无会话'), 'No sessions yet')
  assert.equal(translateDemoText('加载失败：网络异常'), 'Could not load: Network error')
  assert.equal(translateDemoText('代码'), 'Code')
})
