/** Translate the original OJ/Agent chrome in the temporary English build. */
const phrases: Record<string, string> = {
  '检测到这是另一道题目。当前会话保持不变，问题不会写入当前题目的上下文。': 'This is a different problem. The current session stays unchanged and your question will not be added to its context.',
  '输入题目、截图或追问，我会把对话、题目工作区和板书放在同一个工作台里。': 'Share a problem, screenshot, or follow-up question. The conversation, problem workspace, and boardwork stay together here.',
  '选择助教皮肤，并预览判题过程中的静态状态。': 'Choose a mascot skin and preview its states during judging.',
  '教学计划生成后会出现在这里。': 'The teaching plan will appear here when ready.',
  '当前题目还没有正文摘要。': 'There is no problem summary yet.',
  '本轮问题未写入当前会话': 'This question was not added to the current session',
  '这条来源消息不在当前对话': 'The source message is not in this conversation',
  '请登录后使用 Agent': 'Sign in to use the Agent',
  '打开助教，当前状态：': 'Open the mascot, current state: ',
  '加载更早消息失败': 'Could not load earlier messages',
  '创建失败': 'Could not create',
  '重命名失败': 'Could not rename',
  '加载失败': 'Could not load',
  '网络异常': 'Network error',
  '未知错误': 'Unknown error',
  '浏览器阻止了图片窗口，请允许弹出窗口后重试': 'The browser blocked the image window. Allow pop-ups and try again.',
  '点击展开查看题目内容': 'Click to expand the problem statement',
  '观察这一步中哪些状态发生了变化。': 'Observe which states change in this step.',
  '停止尚未确认，正在核对后台任务。': 'Stop has not been confirmed; checking the background run.',
  '搜索当前对话': 'Search this conversation',
  '在当前对话中搜索': 'Search this conversation',
  '没有匹配结果': 'No matching results',
  '开始一次答疑': 'Start a conversation',
  '加载更早的消息': 'Load earlier messages',
  '正在加载…': 'Loading…',
  '关闭新版 Accoding Agent': 'Close Accoding Agent',
  '打开新版 Accoding Agent': 'Open Accoding Agent',
  'Accoding Agent 新版工作台': 'Accoding Agent workspace',
  '展开会话栏': 'Expand session rail',
  '收起会话栏': 'Collapse session rail',
  '展开题目白板': 'Expand problem board',
  '收起题目白板': 'Collapse problem board',
  '恢复浮窗': 'Restore floating window',
  '分屏模式': 'Split view',
  '新建独立会话': 'New standalone session',
  '新建此题会话': 'New session for this problem',
  '新建会话': 'New session',
  '新会话': 'New session',
  '搜索会话': 'Search sessions',
  '会话与题目': 'Sessions and problems',
  '暂无会话': 'No sessions yet',
  '暂无题目': 'No problems yet',
  '重命名会话': 'Rename session',
  '删除会话': 'Delete session',
  '当前题目': 'Current problem',
  '暂无题目工作区': 'No problem workspace yet',
  '收起题面': 'Collapse statement',
  '展开题面': 'Expand statement',
  '题目规划': 'Teaching plan',
  '暂无题目规划': 'No teaching plan yet',
  '板书沉淀': 'Boardwork',
  '暂无白板沉淀': 'No boardwork yet',
  '生成图示': 'Generated diagram',
  '图示讲解': 'Diagram walkthrough',
  '生成图示中': 'Generating diagram',
  '正在生成图示': 'Generating diagram',
  '正在回复': 'Preparing reply',
  '图示生成失败，请稍后重试': 'Diagram generation failed. Please try again later.',
  '已生成': 'Ready',
  '暂无图示': 'No diagrams yet',
  '图示预览': 'Diagram preview',
  '图示播放进度': 'Diagram playback progress',
  '正在准备图示预览': 'Preparing diagram preview',
  '上一步图示': 'Previous diagram step',
  '播放或暂停图示': 'Play or pause diagram',
  '播放图示': 'Play diagram',
  '暂停图示': 'Pause diagram',
  '下一步图示': 'Next diagram step',
  '上一帧': 'Previous frame',
  '下一帧': 'Next frame',
  '先看变化前的状态': 'Look at the state before the change',
  '得到这一轮后的状态': 'The resulting state',
  '观察当前状态': 'Observe the current state',
  '输入你的问题...': 'Ask your question in English…',
  '先接住发言，再用问题引导': 'Respond with guiding questions',
  '给关键提示，保留推理空间': 'Give key hints and leave room to reason',
  '少讲背景，直接推进下一步': 'Keep it brief and move forward',
  '按用户问题完整回答': 'Answer the full question directly',
  '反问为主': 'Socratic',
  '教练提示': 'Coached',
  '简洁推进': 'Minimal',
  '直接回答': 'Direct',
  '上传图片': 'Upload image',
  '移除图片': 'Remove image',
  '下载图片': 'Download image',
  '图片不可用': 'Image unavailable',
  '加载图片…': 'Loading image…',
  '正在加载图片…': 'Loading image…',
  '上传的图片': 'Uploaded image',
  '停止生成': 'Stop generating',
  '发送编辑': 'Send edited message',
  '取消编辑': 'Cancel editing',
  '编辑重发': 'Edit and resend',
  '删除消息': 'Delete message',
  '正在生成': 'Generating',
  '继续显示内容': 'Show more',
  '已复制': 'Copied',
  '复制': 'Copy',
  '收起': 'Collapse',
  '展开': 'Expand',
  '更多设置': 'More settings',
  '关闭设置': 'Close settings',
  '设置分类': 'Settings categories',
  '助教外观': 'Mascot appearance',
  '助教皮肤': 'Mascot skin',
  '界面字号': 'Text size',
  '全局调整文字大小': 'Adjust text size throughout the workspace',
  '状态预览': 'State preview',
  '助教状态预览': 'Mascot state preview',
  '正在加载外观设置': 'Loading appearance settings',
  '正在加载': 'Loading',
  '正在保存': 'Saving',
  '已同步': 'Saved',
  '重试': 'Retry',
  '外观': 'Appearance',
  '搜索': 'Search',
  '会话': 'Sessions',
  '题目': 'Problems',
  '图示': 'Diagrams',
  '设置': 'Settings',
  '皮肤': 'Skin',
  '粒度': 'Guidance',
  '发送': 'Send',
  '关闭': 'Close',
  '暂无摘要': 'No summary yet',
  '新题目': 'New problem',
  '步骤': 'Step',
  '第': 'Step ',
  '步': ' step',
  '个会话': ' sessions',
  '图片': 'Image',
  '代码': 'Code',
  '失败': 'Failed',
  '暂停': 'Pause',
  '播放': 'Play',
  '起点': 'Start',
  '变化': 'Change',
  '结果': 'Result',
  '新增': 'Add',
  '比较': 'Compare',
  '关注': 'Focus',
  '高亮': 'Highlight',
  '移动': 'Move',
  '弹出': 'Pop',
  '入栈': 'Push',
  '移除': 'Remove',
  '更新': 'Update',
  '无': 'None',
  '小': 'Small',
  '中': 'Medium',
  '大': 'Large',
  '我': 'Me',
}

const replacements = Object.entries(phrases).sort((a, b) => b[0].length - a[0].length)
const han = /[\u3400-\u9fff]/u

export function translateDemoText(source: string): string {
  if (!han.test(source)) return source
  let result = source
  for (const [chinese, english] of replacements) result = result.split(chinese).join(english)
  return result.split('：').join(': ').split('，').join(', ').split('。').join('.')
}

export function installEnglishDemoDomTranslation(): void {
  const translateElement = (root: Node) => {
    if (root.nodeType === Node.TEXT_NODE) {
      const parent = root.parentElement
      if (parent?.closest('.chat-message.user .message-content, .chat-message.assistant .message-content, pre, code, textarea, script, style')) return
      const oldText = root.nodeValue || ''
      const nextText = translateDemoText(oldText)
      if (nextText !== oldText) root.nodeValue = nextText
      return
    }
    if (!(root instanceof Element)) return
    for (const element of [root, ...root.querySelectorAll('*')]) {
      for (const attribute of ['aria-label', 'placeholder', 'title', 'alt']) {
        const oldValue = element.getAttribute(attribute)
        if (oldValue && han.test(oldValue)) element.setAttribute(attribute, translateDemoText(oldValue))
      }
      for (const child of element.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) translateElement(child)
      }
    }
  }
  translateElement(document.body)
  const observer = new MutationObserver(records => {
    for (const record of records) {
      for (const child of record.addedNodes) translateElement(child)
    }
  })
  observer.observe(document.body, {
    childList: true,
    subtree: true,
  })
}
