export const useGlobalNotifyStore = defineStore("notification", {
  state: () => ({
    isVisible: false,
    message: "请全体用户注意，系统计划于2024年12月1日0点整进行系统维护，届时将无法登录使用。详情请关注官方公告",
  }),
  actions: {
    setMessage(msg: string) {
      this.message = msg;
    },
    closeGlobalNotify() {
      this.isVisible = false;
      // TODO 在浏览器存储用户是否已关闭通知，同时需要新增逻辑判断是否产生了新的通知
    },
  },
});
