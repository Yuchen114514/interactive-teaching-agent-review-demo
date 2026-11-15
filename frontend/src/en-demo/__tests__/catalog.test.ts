import assert from 'node:assert/strict'
import test from 'node:test'
import { demoDifficultyRating, demoProblems, findDemoProblem, searchDemoProblems } from '../catalog'

test('English demo exposes only sequential reserved IDs', () => {
  assert.deepEqual(demoProblems.map(problem => problem.id), [1000001, 1000002, 1000003, 1000004, 1000005])
  assert.equal(findDemoProblem(12), undefined)
  assert.equal(findDemoProblem(1000002)?.title, 'Longest Increasing Subsequence')
})

test('English demo search never includes production problem data', () => {
  assert.deepEqual(searchDemoProblems('breadth').map(problem => problem.id), [1000003])
  assert.deepEqual(searchDemoProblems('cargo').map(problem => problem.id), [1000004])
  assert.deepEqual(searchDemoProblems('archive').map(problem => problem.id), [1000005])
  assert.deepEqual(searchDemoProblems('中文'), [])
})

test('English demo preserves the three displayed difficulty levels', () => {
  assert.equal(demoDifficultyRating('Easy'), 1)
  assert.equal(demoDifficultyRating('Medium'), 2)
  assert.equal(demoDifficultyRating('Hard'), 3)
})
