import { ElNotification } from "element-plus";

export const openNotification = (
  title: string,
  message: string,
  type: string,
  position: string = "top-right"
) => {
  (ElNotification as any)({
    title: title,
    message: message,
    type: type,
    position: position,
  });
};

export const convertDateFormat = (timestamp: string) => {
  const dateParts = timestamp.split(" ");
  return `${dateParts[0]} ${dateParts[1].replace(/-/g, ":")}`;
};

/**
 * 格式化相对时间
 *
 * @param now 当前时间
 * @param timestamp 需要格式化的时间戳
 * @returns 格式化后的相对时间字符串
 */
export const formatRelativeTime = (now: Date, timestamp: string) => {
  const nowValue = now.getTime();
  const timestampValue = new Date(timestamp).getTime();
  const diff = Math.abs(nowValue - timestampValue);
  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (seconds < 60) {
    return `${seconds}秒前`;
  } else if (minutes < 60) {
    const remainingSeconds = seconds % 60;
    return remainingSeconds > 0
      ? `${minutes}分钟${remainingSeconds}秒前`
      : `${minutes}分钟前`;
  } else if (hours < 24) {
    const remainingMinutes = minutes % 60;
    return remainingMinutes > 0
      ? `${hours}小时${remainingMinutes}分钟前`
      : `${hours}小时前`;
  } else {
    // const remainingHours = hours % 24;
    // return remainingHours > 0
    //   ? `${days}天${remainingHours}小时前`
    //   : `${days}天前`;
    return timestamp;
  }
};

export const formatProblemIndex = (index: number) => {
  const base = 26; // A-Z的个数
  const letterCode = 65; // ASCII码中大写字母A的值
  const suffix = Math.floor(index / base);
  const suffixStr = suffix > 0 ? String(suffix + 1) : "";
  const letterIndex = index % base;
  const letter = String.fromCharCode(letterCode + letterIndex);
  return letter + suffixStr;
};

export const getDateAndDayOfWeek = (
  year: number,
  quarter: number,
  index: number,
) => {
  // 设定每个季度的起始和结束月份
  const quarters = [
    { startMonth: 0, endMonth: 2 }, // 第一季度 (1月, 2月, 3月)
    { startMonth: 3, endMonth: 5 }, // 第二季度 (4月, 5月, 6月)
    { startMonth: 6, endMonth: 8 }, // 第三季度 (7月, 8月, 9月)
    { startMonth: 9, endMonth: 11 }, // 第四季度 (10月, 11月, 12月)
  ];

  // 获取当前季度的起始和结束月份
  const { startMonth, endMonth } = quarters[quarter - 1];

  // 计算该季度的第一天是当年的第几天
  let dayOfYearStart = 0;
  for (let m = 0; m < startMonth; m++) {
    dayOfYearStart += new Date(year, m + 1, 0).getDate(); // 获取前一个月的天数
  }

  // 计算目标日期是当年的第几天
  const targetDayOfYear = dayOfYearStart + 1 + index; // 加1是因为index从0开始

  // 创建目标日期的Date对象
  const targetDate = new Date(Date.UTC(year, 0, targetDayOfYear));

  // 获取目标日期的月和日
  const month = targetDate.getUTCMonth() + 1; // getUTCMonth() 是从 0 开始的
  const day = targetDate.getUTCDate();

  // 获取星期几，Date对象的getDay()方法返回的是一个0（代表星期日）到6（代表星期六）之间的整数
  const dayOfWeekIndex = targetDate.getUTCDay();
  const daysOfWeek = [
    "星期日",
    "星期一",
    "星期二",
    "星期三",
    "星期四",
    "星期五",
    "星期六",
  ];
  const dayOfWeek = daysOfWeek[dayOfWeekIndex];

  // 返回日期和星期几
  return `${year}/${month}/${day} ${dayOfWeek}`;
};

export const formatDateTime = (date: Date) => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0"); // 月份从0开始，加1后补零
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
};

export const formatDuration = (seconds: number) => {
  if (seconds === 0) {
    return "0s";
  }

  const days = Math.floor(seconds / (24 * 60 * 60));
  const hours = Math.floor((seconds % (24 * 60 * 60)) / (60 * 60));
  const minutes = Math.floor((seconds % (60 * 60)) / 60);
  const remainingSeconds = seconds % 60;

  let result = "";
  if (days > 0) {
    result += `${days}天`;
  }
  if (hours > 0) {
    result += `${hours}小时`;
  }
  if (minutes > 0) {
    result += `${minutes}分钟`;
  }
  if (remainingSeconds > 0 || result === "") {
    result += `${remainingSeconds}s`;
  }

  return result;
};

export const penaltyFormatter = (penalty: number) => {
  let str = Math.abs(penalty).toString();
  while (str.length < 6) {
    str = "0" + str;
  }
  if (str.length > 6) {
    str =
      str.slice(0, str.length - 4) +
      ":" +
      str.slice(str.length - 4, str.length - 2) +
      ":" +
      str.slice(str.length - 2);
  } else if (str.length === 6) {
    str = str.slice(0, 4) + ":" + str.slice(4, 6);
  }
  return str;
};

export const getPenaltyClassName = (penalty: number) => {
  const defaultClass = "p-0 rounded-lg hover:underline cursor-pointer ";
  let currentClass = "";
  if (penalty < 2) currentClass = "";
  else if (penalty === 2) currentClass = "bg-red-50 text-red-500";
  else if (penalty === 3) currentClass = "bg-amber-50 text-amber-600";
  else if (penalty === 4) currentClass = "bg-ojSuccess-50 text-green-700";
  else currentClass = "bg-blue-50 text-blue-500";
  return defaultClass + currentClass;
};
