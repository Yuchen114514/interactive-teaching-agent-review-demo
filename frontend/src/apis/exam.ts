import axios from "axios";
import type { SubmissionFilterType } from "@/types";
import type { Exam } from "@/views/Exam/consts";

/**
 * 获取考试访问类型服务
 *
 * @param examId 考试ID
 * @param token 授权令牌
 * @returns 返回一个Promise对象，该对象解析为一个包含考试访问类型信息的对象
 */
export const getExamAccessTypeService = (examId: number, token: string) => {
  return axios({
    method: "GET",
    url: `/api/contest/${examId}/accessLevel`,
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取考试列表
 *
 * @param token 用户认证令牌
 * @param page 当前页码
 * @param pageSize 每页显示的条目数
 * @param titleSearchKey 标题搜索关键字
 * @param classNameSearchKey 课程搜索关键字
 * @returns 返回考试列表的Promise对象
 */
// TODO 方法存在问题，无法search
export const getExamListService = (
  token: string,
  page: number,
  pageSize: number,
  titleSearchKey: string,
  classNameSearchKey: string,
) => {
  return axios({
    method: "GET",
    url: "/api/contest/list",
    params: {
      page: page,
      limit: pageSize,
      titleSearch: titleSearchKey,
      AuthorSearch: classNameSearchKey,
    },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取考试列表分页数量
 *
 * @param token 认证令牌
 * @param pageSize 每页显示数量
 * @param titleSearchKey 标题搜索关键词
 * @param classNameSearchKey 课程名称搜索关键词
 * @returns 返回一个Promise，解析为包含分页数量的对象
 */
// TODO 方法存在问题，无法search
export const getExamListPageNumService = (
  token: string,
  pageSize: number,
  titleSearchKey: string,
  classNameSearchKey: string,
) => {
  return axios({
    method: "GET",
    url: "/api/contest/list/pagenum",
    params: {
      limit: pageSize,
      titleSearch: titleSearchKey,
      AuthorSearch: classNameSearchKey,
    },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取考试信息
 *
 * @param examId 考试ID
 * @param token 授权令牌
 * @returns 返回考试信息的Promise对象
 */
export const getExamInfoService = (examId: number, token: string) => {
  return axios({
    method: "GET",
    url: `/api/contest/${examId}/info`,
    data: { id: examId },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取服务器时间
 *
 * @returns 返回一个 Promise，解析为服务器时间的响应对象
 */
export const getServerTimeService = () => {
  return axios({
    method: "GET",
    url: "/api/contest/server-time",
  });
};

/**
 * 获取考试题目列表
 *
 * @param examId 考试ID
 * @param token 认证令牌
 * @returns 返回Promise对象，解析为包含题目列表的axios响应对象
 */
export const getExamProblemsService = (examId: number, token: string) => {
  return axios({
    method: "GET",
    url: `/api/contest/${examId}/problem`,
    data: {
      examId,
    },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取考试题目状态
 *
 * @param examId 考试ID
 * @param token 授权令牌
 * @returns 返回包含题目状态的Promise对象
 */
export const getExamProblemStatusService = (examId: number, token: string) => {
  return axios({
    method: "GET",
    url: `/api/contest/${examId}/problemStatus`,
    headers: {
      Authorization: token,
    },
  });
};

export const recordExamProblemStartService = (
  token: string,
  examId: number,
  problemId: number,
) => {
  return axios({
    method: "POST",
    url: "/api/api/agents/exam/timings/start",
    data: {
      exam_id: examId,
      problem_id: problemId,
    },
    headers: {
      "X-Accoding-Session": token,
    },
  });
};

/**
 * 获取考试排名列表
 *
 * @param examId 考试ID
 * @param token 授权令牌
 * @returns 返回一个包含考试排名列表的Promise对象
 */
export const getExamRankListService = (examId: number, token: string) => {
  return axios({
    method: "GET",
    url: `/api/contest/${examId}/rank`,
    headers: {
      Authorization: token,
    },
  });
};

export const getExamSubmissionListPageCountService = (
  examId: number,
  token: string,
  page: number,
  pageSize: number,
  filter?: SubmissionFilterType,
) => {
  return axios({
    method: "GET",
    url: `/api/submission/exam/${examId}/pageNum`,
    params: {
      page: page,
      pageSize: pageSize,
      ...filter,
    },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取考试提交列表
 *
 * @param examId 考试ID
 * @param token 授权令牌
 * @returns 返回一个Promise对象，解析为考试提交列表的响应数据
 */
export const getExamSubmissionListService = (
  examId: number,
  token: string,
  page: number,
  pageSize: number,
  filter?: SubmissionFilterType,
) => {
  return axios({
    method: "GET",
    url: `/api/submission/exam/${examId}`,
    params: {
      page: page,
      pageSize: pageSize,
      ...filter,
    },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 添加考试服务
 *
 * @param token 认证令牌
 * @param exam 考试对象
 * @returns 返回添加考试请求的 Promise 对象
 */
export const addExamService = (token: string, exam: Exam) => {
  return axios({
    method: "POST",
    url: `/api/admin/contest/create`,
    data: { ...exam },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 设置考试信息
 *
 * @param token 认证令牌
 * @param data 考试信息数据
 * @returns 返回 axios 响应
 */
export const editExamBaseInfoService = (token: string, data: any) => {
  return axios({
    method: "POST",
    url: "/api/admin/contest/edit/info",
    data: { ...data },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 设置考试题目列表服务
 *
 * @param token 用户令牌
 * @param data 请求数据
 * @returns 返回 axios 请求的 Promise 对象
 */
export const editExamProblemListService = (token: string, data: any) => {
  return axios({
    method: "POST",
    url: "/api/admin/contest/edit/problems",
    data: { ...data },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取考试服务的路由访问权限
 *
 * @param token 访问令牌
 * @param examId 考试ID
 * @returns 返回一个Promise对象，包含路由访问权限信息
 */
export const getRouterAccessOfExamService = (token: string, examId: number) => {
  return axios({
    method: "GET",
    url: `/api/contest/${examId}/access`,
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取考试完成状态的接口服务
 *
 * @param token 用户访问令牌
 * @param examId 考试ID
 * @returns 返回考试完成状态的Promise对象
 */
export const getExamFinishStatusService = (token: string, examId: number) => {
  return axios({
    method: "GET",
    url: `/api/contest/${examId}/access`,
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 发送考试通知服务
 *
 * @param token 用户认证令牌
 * @param data 通知数据
 * @returns 返回请求结果
 */
export const postExamNotifyService = (token: string, data: any) => {
  return axios({
    method: "POST",
    url: `/api/admin/contest/issue`,
    data: { ...data },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取考试通知服务
 *
 * @param examId 考试ID
 * @returns 返回请求结果
 */
export const getExamNotifyService = (examId: number) => {
  return axios({
    method: "GET",
    url: `/api/contest/${examId}/issue`,
  });
}
