import rawProblems from '../../../demo/english_problems.json'

export type DemoProblem = {
  id: number
  title: string
  topic: string
  difficulty: string
  timeLimit: string
  memoryLimit: string
  statement: string
  input: string
  output: string
  constraints: string
  examples: { input: string; output: string; explanation: string }[]
}

export const demoProblems: DemoProblem[] = rawProblems as DemoProblem[]

export function demoDifficultyRating(difficulty: string): number {
  if (difficulty === 'Easy') return 1
  if (difficulty === 'Hard') return 3
  return 2
}

export function findDemoProblem(id: number): DemoProblem | undefined {
  return demoProblems.find(problem => problem.id === id)
}

export function searchDemoProblems(query: string): DemoProblem[] {
  const needle = query.trim().toLowerCase()
  if (!needle) return demoProblems
  return demoProblems.filter(problem =>
    [String(problem.id), problem.title, problem.topic, problem.difficulty]
      .some(value => value.toLowerCase().includes(needle)),
  )
}

export function problemContext(problem: DemoProblem): string {
  return [
    `I am working on the English demo problem "${problem.title}".`,
    `Problem statement: ${problem.statement}`,
    `Input: ${problem.input}`,
    `Output: ${problem.output}`,
    `Constraints: ${problem.constraints}`,
    ...problem.examples.map((example, index) =>
      `Example ${index + 1}: input ${JSON.stringify(example.input)}, output ${JSON.stringify(example.output)}.`,
    ),
  ].join('\n')
}
