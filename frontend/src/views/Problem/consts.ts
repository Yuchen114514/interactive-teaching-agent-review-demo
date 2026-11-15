export const toolbars = {
  bold: true, // 粗体
  italic: true, // 斜体
  header: true, // 标题
  underline: true, // 下划线
  strikethrough: true, // 中划线
  mark: true, // 标记
  superscript: true, // 上角标
  subscript: true, // 下角标
  quote: true, // 引用
  ol: true, // 有序列表
  ul: true, // 无序列表
  link: true, // 链接
  imagelink: true, // 图片链接
  code: true, // code
  table: true, // 表格
  fullscreen: false, // 全屏编辑
  readmodel: false, // 沉浸式阅读
  htmlcode: false, // 展示html源码
  help: true, // 帮助
  /* 1.3.5 */
  undo: true, // 上一步
  redo: true, // 下一步
  trash: true, // 清空
  save: false, // 保存（触发events中的save事件）
  /* 1.4.2 */
  navigation: true, // 导航目录
  /* 2.1.8 */
  alignleft: true, // 左对齐
  aligncenter: true, // 居中
  alignright: true, // 右对齐
  /* 2.2.1 */
  subfield: true, // 单双栏模式
  preview: true, // 预览
};

export const accessTypes = [
  {
    label: "公开",
    value: "public",
  },
  {
    label: "私有",
    value: "private",
  },
  {
    label: "组内可见",
    value: "protected",
  },
];

export type Tag = {
  id: number;
  name: string;
  count?: number;
};

export type DataPoint = {
  id?: number;
  input: string | File; // file name
  output: string | File; // file name
  weight: number;
};

export const SubmissionResults = [
  {
    label: "WT",
    value: "WT",
    tagType: "info",
  },
  {
    label: "JG",
    value: "JG",
    tagType: "primary",
  },
  {
    label: "AC",
    value: "AC",
    tagType: "success",
  },
  {
    label: "WA",
    value: "WA",
    tagType: "danger",
  },
  {
    label: "PE",
    value: "PE",
    tagType: "warning",
  },
  {
    label: "CE",
    value: "CE",
    tagType: "danger",
  },
  {
    label: "TLE",
    value: "TLE",
    tagType: "danger",
  },
  {
    label: "MLE",
    value: "MLE",
    tagType: "danger",
  },
  {
    label: "RE",
    value: "RE",
    tagType: "danger",
  },
  {
    label: "OE",
    value: "OE",
    tagType: "danger",
  },
];

export const problemStatusMap: Record<string, string> = {
  "-1": "danger",
  "0": "info",
  "1": "success",
};

export type ProblemEditInfo = {
  // base info
  problemId: number;
  classId: number;

  title: string;
  content: string;
  difficulty: number;

  timeLimit: number;
  memoryLimit: number;
  stackLimit: number;

  access: string;
  languages: Array<number>;
  tags: Array<Tag>;

  createTime: string;
  updateTime: string;

  // spj about
  isSpecialJudge: number;
  specialJudge: {
    specialJudgeLanguageName: number;
    specialJudgeCode: File;
  } | null;

  // un-spj about
  unSpecialJudge: {
    compareLevel: number;
    peCompareLevel: number;
    tabWidth: number;
    isIgnoreCase: boolean;
  } | null;

  testDataZip: File | null;
  testData: Array<DataPoint>;
};

const FileTypeCheckMap: Record<string, string[]> = {
  c: ["c"],
  cpp: ["c++"],
  h: ["c", "c++"],
  py: ["python", "python2", "python3"],
  java: ["java"],
};

export const checkFileType = (fileType: string, lang: string) => {
  return FileTypeCheckMap[fileType]?.includes(lang);
}
