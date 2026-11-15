import axios from "axios";

/**
 * 登录
 * @param username
 * @param password
 * @returns
 */
export const login = (username: string, password: string) => {
  return axios({
    method: "post",
    url: "/api/login",
    data: { username, password },
  });
};

/**
 * 注册
 * @param registerForm
 * @returns
 */
export const register = (registerForm: {
  username: string;
  password: string;
  nickname: string;
  description?: string;
  school: string;
  major: string;
  studentId: string;
  realName?: string;
}) => {
  return axios({
    method: "post",
    url: "/api/register",
    data: {
      username: registerForm.username,
      password: registerForm.password,
      nickname: registerForm.nickname,
      description: registerForm.description ? registerForm.description : "",
      school: registerForm.school,
      major: registerForm.major,
      studentId: registerForm.studentId,
      realName: registerForm.realName ? registerForm.realName : "",
    },
  });
};

/**
 * 获取用户信息
 * @param token
 * @returns
 */
export const getUserInfo = (token: string) => {
  return axios({
    method: "GET",
    url: "/api/user/info",
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 获取用户身份权限
 * @param token
 * @returns
 */
export const getUserRole = (token: string) => {
  return axios({
    method: "GET",
    url: "/api/isSuperAdmin",
    headers: {
      Authorization: token,
    },
  });
};

/**
 * 编辑用户信息
 * @param editProfilForm
 * @returns
 */
export const editUserInfo = (editProfilForm: {
  token: string;
  nickname: string;
  studentId: string;
  school: string;
  description: string | undefined;
  realname: string | undefined;
  major: string | undefined;
}) => {
  return axios({
    method: "post",
    url: "/api/user/edit",
    headers: {
      Authorization: editProfilForm.token,
    },
    data: {
      nickname: editProfilForm.nickname,
      studentId: editProfilForm.studentId,
      school: editProfilForm.school,
      description: editProfilForm.description,
      realname: editProfilForm.realname,
      major: editProfilForm.major,
    },
  });
};

/**
 * 更新密码
 * @param oldPassword
 * @param newPassword
 * @param token
 * @returns
 */
export const updatePasswordService = (
  oldPassword: string,
  newPassword: string,
  token: string,
) => {
  return axios({
    method: "POST",
    url: "/api/user/updatepwd",
    headers: {
      Authorization: token,
    },
    data: {
      oldPassword,
      newPassword,
    },
  });
};

/**
 * 发送验证码
 * @param email
 * @returns
 */
export const sendCodeService = (email: string) => {
  return axios({
    method: "POST",
    url: "/api/user/code",
    data: {
      email,
    },
  });
};

/**
 * 验证验证码
 * @param email
 * @param code
 * @returns
 */
export const verifyCodeService = (email: string, code: string) => {
  return axios({
    method: "POST",
    url: "/api/user/verf",
    data: {
      email,
      code,
    },
  });
};


export const getUserWeeklySubmissionListService = (token: string) => {
  return axios({
    method: "GET",
    url: "/api/user/sub-week",
    headers: {
      Authorization: token,
    },
  });
}

export const getUserSubmissionTypeListService = (token: string) => {
  return axios({
    method: "GET",
    url: "/api/user/sub-type",
    headers: {
      Authorization: token,
    },
  });
}
