import { defineStore } from "pinia";
import { getActiveRouter } from "@/router/activeRouter";
import { openNotification } from "@/hooks";
import { accodingArt } from "@/style/accoding";
import { login, getUserInfo, getUserRole } from "@/apis/user";
import localforage from "localforage";
import { ElMessage } from "element-plus";
import { DEMO_PAUSED_KEY, DEMO_RECOVERY_MARKER_KEY, DEMO_TOKEN_KEY, DEMO_USER_KEY, isDemoTokenUsable, isEnglishDemo } from "@/en-demo/mode";

let demoRestoreInFlight: Promise<boolean> | null = null;

export interface User {
  id: string;
  role: boolean; // true为管理员，false为普通用户
  username: string;
  nickname: string;
  studentId: string;
  school?: string;
  phone?: string;
  realname?: string;
  major?: string;
  description?: string;
}

export const useAccodingStore = defineStore("accoding", {
  state: () => ({
    // TODO 新增系统状态, 从数据库当中获取
    ACCODING_FIX_STATUS: false as boolean,
    ACCODING_FIX_START_TIME: "2024-12-01 08:00:00" as string,
    ACCODING_FIX_MESSAGE: "系统维护中，请耐心等待" as string,

    isLogin: false as boolean,
    demoSessionExpired: false as boolean,
    user: {} as User,
    token: "" as string,
    lastLoginTime: null as Date | null,
  }),
  actions: {
    _init() {
      if (isEnglishDemo) {
        if (localStorage.getItem(DEMO_PAUSED_KEY) === "1") return;
        const demoToken = sessionStorage.getItem(DEMO_TOKEN_KEY);
        const demoUser = sessionStorage.getItem(DEMO_USER_KEY);
        if (demoToken && demoUser && isDemoTokenUsable(demoToken)) {
          try {
            this.user = JSON.parse(demoUser) as User;
            this.token = demoToken;
            this.isLogin = true;
            return;
          } catch { /* Clear only the isolated demo identity below. */ }
        }
        // Keep an expired signed token until the restore endpoint can link it
        // to the same persisted demo account and set its recovery cookie.
        this.demoSessionExpired = Boolean(demoToken);
        return;
      }
      console.log(accodingArt);
      const isLogin: boolean = localStorage.getItem("isLogin") === "true";
      const token: string | null = localStorage.getItem("token");
      const userJSON = localStorage.getItem("user");
      const lastLoginTimeStr = localStorage.getItem("lastLoginTime");

      // 判断是否登录，如果登录则从缓存中获取用户信息
      if (isLogin && token && userJSON && lastLoginTimeStr) {
        const user = JSON.parse(userJSON) as User;
        const lastLoginTime = new Date(lastLoginTimeStr).getTime();
        const currentTime = new Date().getTime();
        const diffTime = Math.abs(currentTime - lastLoginTime); // ms

        // 判断登录时长是否超过三天
        const validTime = 3 * 24 * 3600 * 1000;
        if (diffTime < validTime) {
          this.isLogin = true;
          this.token = token;
          this.user = user;
          this.setLastLoginTime(); // 更新最后登录时间
        } else {
          this._logout();
        }
      } else {
        this.isLogin = false;
        this._logout();
      }
    },
    setLogin(isLogin: boolean) {
      this.isLogin = isLogin;
      localStorage.setItem("isLogin", isLogin ? "true" : "false");
    },
    getLoginFromLoaclStorage() {
      return localStorage.getItem("isLogin") === "true";
    },
    setToken(token: string) {
      this.token = token;
      localStorage.setItem("token", token);
    },
    setUser(user: User) {
      this.user = user;
      localStorage.setItem("user", JSON.stringify(user));
    },
    setLastLoginTime() {
      const currentTime = new Date();
      this.lastLoginTime = currentTime;
      localStorage.setItem("lastLoginTime", currentTime.toLocaleString());
    },
    async _login(username: string, password: string) {
      const loginRes = (await login(username, password)).data;
      if (loginRes.status === 200) {
        const nextToken = loginRes.data.token;
        const userInfo = (await getUserInfo(nextToken)).data;
        if (userInfo.status === 200) {
          const getUser: User = {
            id: "",
            username: "",
            nickname: "",
            role: false,
            school: "",
            studentId: "",
            major: "",
            description: "",
            realname: "",
          };
          getUser.id = loginRes.data.uid;
          getUser.username = userInfo.data.email;
          getUser.nickname = userInfo.data.nickname;
          getUser.studentId = userInfo.data.student_id;
          getUser.realname = userInfo.data.realname;
          getUser.school = userInfo.data.school;
          getUser.major = userInfo.data.major;
          getUser.description = userInfo.data.description;

          const userRole = (await getUserRole(nextToken)).data;
          if (userRole.status === 200) {
            getUser.role = userRole.data;
          } else {
            openNotification(
              "登录信息反馈",
              "获取用户角色失败，请重新登录！",
              "error",
            );
          }
          // Publish the complete identity in the same tick; appearance watchers
          // must never combine an old account ID with the new account token.
          this.setUser(getUser);
          this.setToken(nextToken);
          this.setLogin(true);
        } else {
          openNotification(
            "登录信息反馈",
            "获取用户信息失败，请重新登录！",
            "error",
          );
          return;
        }
        this.setLastLoginTime();
        openNotification("登录信息反馈", "登录成功，欢迎回来！", "success");
        getActiveRouter().push("/allClasses");
      } else {
        openNotification("登录信息反馈", loginRes.msg, "error");
      }
    },
    async _loginDemoGuest() {
      if (!isEnglishDemo) return;
      const previousToken = sessionStorage.getItem(DEMO_TOKEN_KEY);
      const response = await fetch("/en-demo/api/en-demo/guest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ previous_token: previousToken }),
      });
      if (!response.ok) throw new Error("Demo sign-in is temporarily unavailable.");
      const payload = await response.json() as { token?: string; visitor_id?: number };
      this._applyDemoGuest(payload);
      localStorage.removeItem(DEMO_PAUSED_KEY);
      ElMessage.success("Welcome to the English demo.");
      await getActiveRouter().push("/allProblems");
    },
    _applyDemoGuest(payload: { token?: string; visitor_id?: number }) {
      if (!payload.token || !payload.visitor_id) throw new Error("Demo sign-in is temporarily unavailable.");
      const user: User = {
        id: String(payload.visitor_id),
        role: false,
        username: `demo-${payload.visitor_id}`,
        nickname: "Demo visitor",
        studentId: String(payload.visitor_id),
        school: "",
        major: "",
        description: "",
        realname: "",
      };
      sessionStorage.setItem(DEMO_TOKEN_KEY, payload.token);
      sessionStorage.setItem(DEMO_USER_KEY, JSON.stringify(user));
      localStorage.setItem(DEMO_RECOVERY_MARKER_KEY, "1");
      this.user = user;
      this.token = payload.token;
      this.isLogin = true;
      this.demoSessionExpired = false;
    },
    async _restoreDemoGuest(force = false): Promise<boolean> {
      if (!isEnglishDemo || localStorage.getItem(DEMO_PAUSED_KEY) === "1") return false;
      const previousToken = this.token || sessionStorage.getItem(DEMO_TOKEN_KEY);
      const remembered = localStorage.getItem(DEMO_RECOVERY_MARKER_KEY) === "1";
      if (!force && this.isLogin && isDemoTokenUsable(this.token) && remembered) return true;
      if (!previousToken && !remembered) return false;
      if (demoRestoreInFlight) return demoRestoreInFlight;
      demoRestoreInFlight = (async () => {
        try {
          const response = await fetch("/en-demo/api/en-demo/guest/restore", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ previous_token: previousToken }),
          });
          if (!response.ok) throw new Error("Demo visit cannot be restored.");
          if (localStorage.getItem(DEMO_PAUSED_KEY) === "1") return false;
          this._applyDemoGuest(await response.json() as { token?: string; visitor_id?: number });
          return true;
        } catch {
          this._clearDemoGuest(true);
          localStorage.removeItem(DEMO_RECOVERY_MARKER_KEY);
          return false;
        }
      })();
      try { return await demoRestoreInFlight; }
      finally { demoRestoreInFlight = null; }
    },
    _clearDemoGuest(expired = false) {
      if (!isEnglishDemo) return;
      this.isLogin = false;
      this.token = "";
      this.user = {} as User;
      this.lastLoginTime = null;
      this.demoSessionExpired = expired;
      sessionStorage.removeItem(DEMO_TOKEN_KEY);
      sessionStorage.removeItem(DEMO_USER_KEY);
    },
    _logout() {
      if (isEnglishDemo) {
        localStorage.setItem(DEMO_PAUSED_KEY, "1");
        this._clearDemoGuest();
        ElMessage.info("You have left the demo.");
        return;
      }
      this.isLogin = false;
      this.token = "";
      this.user = {} as User;
      this.lastLoginTime = null;
      // 清空缓存
      const appearanceCache = Object.keys(localStorage)
        .filter(key => key.startsWith("accoding:appearance:"))
        .map(key => [key, localStorage.getItem(key)!] as const);
      localStorage.clear();
      for (const [key, value] of appearanceCache) localStorage.setItem(key, value);
      localforage.clear();
      ElMessage({
        message: "请登录后进入系统",
        type: "warning",
      });
    },
    async _getinfo() {
      const userInfo = (await getUserInfo(this.token)).data;
      if (userInfo.status === 200) {
        const getUser: User = {
          id: "",
          username: "",
          nickname: "",
          role: false,
          school: "",
          studentId: "",
          major: "",
          description: "",
          realname: "",
        };
        getUser.id = this.user.id;
        getUser.username = userInfo.data.email;
        getUser.nickname = userInfo.data.nickname;
        getUser.studentId = userInfo.data.student_id;
        getUser.realname = userInfo.data.realname;
        getUser.school = userInfo.data.school;
        getUser.major = userInfo.data.major;
        getUser.description = userInfo.data.description;
        getUser.role = this.user.role;
        this.setUser(getUser);
      } else {
        openNotification(
          "用户信息获取反馈",
          "获取用户信息失败，请检查网络！",
          "error",
        );
      }
    },
  },
});
