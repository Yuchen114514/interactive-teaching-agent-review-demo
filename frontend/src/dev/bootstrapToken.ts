function readEnv(name: string): string | undefined {
  const value = import.meta.env[name];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

export function bootstrapDevLoginState() {
  if (!import.meta.env.DEV) return;

  const token = readEnv("VITE_DEV_LOGIN_TOKEN");
  if (!token) return;

  const userRaw = readEnv("VITE_DEV_LOGIN_USER");
  const user = userRaw ? userRaw : JSON.stringify({
    id: "4",
    username: "dev-user",
    nickname: "开发调试用户",
    studentId: "4",
    role: false,
    school: "",
    major: "",
    description: "",
    realname: "",
  });

  localStorage.setItem("isLogin", "true");
  localStorage.setItem("token", token);
  localStorage.setItem("user", user);
  localStorage.setItem("lastLoginTime", new Date().toLocaleString());
}
