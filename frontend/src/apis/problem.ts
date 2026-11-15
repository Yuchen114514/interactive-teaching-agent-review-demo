// TODO 获取当前用户的过题情况(支持单独传入题目id或者题目id数组, token)

import axios from "axios";
import type { SubmissionFilterType, ProblemBaseInfoType } from "@/types";

/**
 * 获取题目列表
 *
 * @param token 用户身份认证令牌
 * @param page 当前页码
 * @param limit 每页显示的记录数
 * @returns Promise<AxiosResponse> 返回 Axios 响应对象，其中包含题目列表数据
 */
export const getProblemListService = (
  token: string,
  page: number,
  pageSize: number,
) => {
  return axios({
    method: "GET",
    url: "/api/problem/search",
    params: {
      page: page,
      limit: pageSize,
    },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取题目列表分页信息
 *
 * @param pageSize 每页显示的记录数
 * @param token 用户授权令牌
 * @returns 返回请求结果
 */
export const getProblemListPageService = (pageSize: number, token: string) => {
  return axios({
    method: "GET",
    url: "/api/problem/searchPageNum",
    params: {
      limit: pageSize,
    },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取搜索到的题目列表
 *
 * @param params 查询参数，用于过滤和排序搜索结果
 * @param token 认证令牌，用于身份验证
 * @returns 返回一个Promise对象，解析后得到搜索结果
 */
// TODO 完善搜索功能，目前不支持tag搜索
export const getSearchedProblemsService = (params: object, token: string) => {
  return axios({
    method: "GET",
    url: "/api/problem/search",
    params: params,
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取搜索题目的分页数据
 *
 * @param params 查询参数
 * @param token 授权令牌
 * @returns axios 的响应结果
 */
export const getSearchedProblemsPageService = (
  params: object,
  token: string,
) => {
  return axios({
    method: "GET",
    url: "/api/problem/searchPageNum",
    params: params,
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取问题信息
 *
 * @param id 问题ID
 * @param token 认证令牌
 * @returns 返回Promise对象，解析后得到问题信息
 */
export const getProblemInfoService = (id: number, token: string) => {
  return axios({
    method: "GET",
    url: `/api/admin/problem/${id}/info`,
    headers: {
      Authorization: token,
    },
  });
};

export type SubmissionPostData = {
  languageId: number;
  content: string;
  codeLength: number;
  examId?: number;
  problemId: number;
  submitTime: string;
  questionTime: string;
};

export const postProblemSubmissionService = (
  token: string,
  data: SubmissionPostData,
) => {
  return axios({
    method: "POST",
    url: `/api/submission`,
    data: { ...data },
    headers: {
      Authorization: token,
    },
  });
};

export const getSubmissionByIdService = (
  submissionId: number,
  token: string,
) => {
  return axios({
    method: "GET",
    url: `/api/submission/${submissionId}`,
    headers: {
      Authorization: token,
    },
  });
};

export const getSubmissionDetailService = (
  submissionId: number,
  token: string,
) => {
  return axios({
    method: "GET",
    url: `/api/submission/${submissionId}/codeDetail`,
    headers: {
      Authorization: token,
    },
  });
};

export const getUserProblemSubmissionListService = (
  problemId: number,
  token: string,
) => {
  return axios({
    method: "GET",
    url: `/api/submission/problem/${problemId}/self`,
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取标签列表服务
 *
 * @returns 返回 axios 请求实例
 */
export const getTagListService = () => {
  return axios({
    method: "GET",
    url: "/api/admin/tag/all",
  });
};

/**
 * 获取所有语言列表的服务函数
 *
 * @returns 返回axios的Promise对象，包含请求结果
 */
export const getLanguageListService = () => {
  return axios({
    method: "GET",
    url: "/api/admin/lang/all",
  });
};

/**
 * 获取问题编辑信息服务
 *
 * @param problemId 问题ID
 * @returns 返回一个Promise对象，该对象在被解析时会返回一个包含问题编辑信息的对象
 */
export const getProblemEditInfoService = (token: string, problemId: number) => {
  return axios({
    method: "GET",
    url: `/api/admin/problem/${problemId}/edit`,
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取问题提交列表的分页数服务
 *
 * @param problemId 问题ID
 * @param token 用户授权令牌
 * @param page 当前页码
 * @param pageSize 每页显示条数
 * @param filter 筛选条件（可选）
 * @returns 返回axios请求结果
 */
export const getProblemSubmissionListPageCountService = (
  problemId: number,
  token: string,
  page: number,
  pageSize: number,
  filter?: SubmissionFilterType,
) => {
  return axios({
    method: "GET",
    url: `/api/submission/problem/${problemId}/pageNum`,
    params: { page: page, pageSize: pageSize, ...filter },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取指定问题的提交记录列表服务
 *
 * @param problemId 问题ID
 * @param token 访问令牌
 * @param page 页码
 * @param pageSize 每页显示条数
 * @param filter 提交记录筛选条件（可选）
 * @returns 提交记录列表的Promise对象
 */
export const getProblemSubmissionListService = (
  problemId: number,
  token: string,
  page: number,
  pageSize: number,
  filter?: SubmissionFilterType,
) => {
  return axios({
    method: "GET",
    url: `/api/submission/problem/${problemId}`,
    params: { page: page, pageSize: pageSize, ...filter },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取所有提交列表的服务函数
 *
 * @param token 用于授权的令牌
 * @param page 当前页码
 * @param pageSize 每页显示的记录数
 * @param filter 过滤条件（可选）
 * @returns 返回axios的Promise对象，包含所有提交列表的数据
 */
export const getAllSubmissionListService = (
  token: string,
  page: number,
  pageSize: number,
  filter?: SubmissionFilterType,
) => {
  return axios({
    method: "GET",
    url: `/api/submission/list`,
    params: { page: page, pageSize: pageSize, ...filter },
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 发送新增题目请求
 *
 * @param token 认证令牌
 * @param problem 包含问题信息的 FormData 对象
 * @returns axios.AxiosPromise<any> axios 响应的 Promise 对象
 */
export const postProblemAddService = (token: string, problem: FormData) => {
  return axios({
    method: "POST",
    url: "/api/admin/problem/create",
    data: problem,
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取问题数据点zip文件的下载服务
 *
 * @param token 用户的授权令牌
 * @param problemId 问题的唯一标识符
 * @returns 返回axios的Promise对象，包含问题数据点的zip文件下载信息
 */
export const getProblemDataPointZipFileService = (
  token: string,
  problemId: number,
) => {
  return axios({
    method: "GET",
    url: `/api/admin/problem/${problemId}/download`,
    headers: {
      Authorization: token,
    },
    responseType: "blob",
  });
};

/**
 * 更新问题基础信息服务
 *
 * @param token 用户令牌
 * @param problemId 问题ID
 * @param problemBaseInfo 问题基础信息表单数据
 * @returns 返回一个Promise对象，解析后包含更新结果
 */
export const updatePrblemBaseInfoService = (
  token: string,
  problemId: number,
  problemBaseInfo: FormData,
) => {
  return axios({
    method: "POST",
    url: `/api/admin/problem/${problemId}/editInfo`,
    data: problemBaseInfo,
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 更新题目设置的服务函数
 *
 * @param token 认证令牌
 * @param problemId 题目ID
 * @param problemSetting 题目设置数据，类型为FormData
 * @returns 返回axios的Promise对象，包含更新操作的结果
 */
export const updateProblemSettingService = (
  token: string,
  problemId: number,
  problemSetting: FormData,
) => {
  return axios({
    method: "POST",
    url: `/api/admin/problem/${problemId}/editSpj`,
    data: problemSetting,
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 更新问题数据点服务
 *
 * @param token 授权令牌
 * @param problemId 问题ID
 * @param dataPointList 测试数据
 * @returns 返回一个Promise对象，解析为axios的响应对象
 */
export const updateProblemDataPointService = (
  token: string,
  problemId: number,
  dataPointList: FormData,
) => {
  return axios({
    method: "POST",
    url: `/api/admin/problem/${problemId}/editTestData`,
    data: dataPointList,
    headers: {
      Authorization: token,
    },
  });
}

export const getRouterAccessOfProblemService = (token: string, problemId: number) => {
  return axios({
    method: "GET",
    url: `/api/problem/${problemId}/access`,
    headers: {
      Authorization: token,
    },
  });
};
