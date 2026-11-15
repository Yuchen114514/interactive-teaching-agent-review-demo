export type Exam = {
  id?: number;
  classId: number;
  title: string;
  description: string;
  startTime: string;
  endTime: string;
  access: string;
  showRank: boolean;
  problems: Array<ExamProblem>;
};

export type ExamProblem = {
  problemId: number;
  title: string;
  score: number;
  order: number;
};
