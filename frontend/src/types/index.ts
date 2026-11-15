export type SubmissionFilterType = {
  submissionId?: number;
  problemId?: number;
  studentId?: number;
  result?: string;
  nickname?: string;
};

export type ProblemType = {};

export type ProblemDataPointType = {
  input: File;
  output: File;
  weight: number;
};

export type ExamType = {};

export type SubmissionListItemType = { submissionId: number; index: number };

export type ProblemBaseInfoType = {
  title: string;
  content: string;
  access: string;
  difficulty: number;
  timeLimit: number;
  memoryLimit: number;
  stackLimit: number;
  languages: Array<number>;
  tags: Array<any>;
};

export type ProblemSettingType = {
  isSpecialJudge: number;
  unSpecialJudge?: {
    compareLevel: number;
    peCompareLevel: number;
    tabWidth: number;
    isIgnoreCase: number;
  };
  SpecialJudge?: {
    specialJudgeLanguageName: string;
    specialJudgeCode: File;
  };
  specialJudgeCodeChange: number;
};

export type ProblemDataListType = {
  testData?: Array<ProblemDataPointType>;
  addTestData?: Array<ProblemDataPointType>;
}
