import { ElMessage } from "element-plus";
import {
  loadAgentSessionWorkspaces,
  loadAgentWorkspaceBlackboard,
  loadAgentWorkspaceDiagramSummaries,
} from "../api/agentApi";
import { useAgentWorkspaceStore } from "../stores/agentWorkspace";
import { captureAgentAccount } from "../utils/agentAccountScope";
import {
  isAgentSessionCacheAvailable,
  readCachedSessionWorkspaces,
  readCachedWorkspaceBlackboard,
  readCachedWorkspaceDiagrams,
  writeCachedSessionWorkspaces,
  writeCachedWorkspaceBlackboard,
  writeCachedWorkspaceDiagrams,
} from "../utils/agentDataCache";

let viewSequence = 0;

export function useAgentWorkspaceBundle() {
  const workspace = useAgentWorkspaceStore();

  async function loadBundle(
    token: string,
    sessionId: string | null | undefined,
    options: { background?: boolean } = {},
  ) {
    const account = captureAgentAccount(token);
    const ownsSession = () =>
      account.isCurrent() &&
      (!sessionId || isAgentSessionCacheAvailable(token, sessionId));
    if (!ownsSession()) return;
    const sequence =
      workspace.currentSessionId === (sessionId ?? null) ? ++viewSequence : -1;
    const ownsView = () =>
      ownsSession() &&
      sequence === viewSequence &&
      workspace.currentSessionId === (sessionId ?? null);
    if (!sessionId) {
      if (ownsView()) {
        workspace.setWorkspaceLoading(false);
        workspace.setWorkspaceBundle({
          workspaces: [],
          blackboard: [],
          diagrams: [],
          selectedWorkspaceId: null,
        });
      }
      return;
    }
    const cachedWorkspaces = await readCachedSessionWorkspaces(
      token,
      sessionId,
    );
    if (!ownsSession()) return;
    if (!options.background && cachedWorkspaces?.data?.length && ownsView()) {
      const selectedWorkspaceId = cachedWorkspaces.data.some(
        (item) => item.workspace_id === workspace.selectedWorkspaceId,
      )
        ? workspace.selectedWorkspaceId
        : (cachedWorkspaces.data[0]?.workspace_id ?? null);
      const [blackboard, diagrams] = selectedWorkspaceId
        ? await Promise.all([
            readCachedWorkspaceBlackboard(token, selectedWorkspaceId),
            readCachedWorkspaceDiagrams(token, selectedWorkspaceId),
          ])
        : [null, null];
      if (ownsView())
        workspace.setWorkspaceBundle({
          workspaces: cachedWorkspaces.data,
          blackboard: blackboard?.data ?? [],
          diagrams: diagrams?.data ?? [],
          selectedWorkspaceId,
          sessionId,
        });
    }
    const showLoading = !options.background && !cachedWorkspaces?.data?.length;
    if (ownsView()) workspace.setWorkspaceLoading(showLoading);
    try {
      const workspaces = await loadAgentSessionWorkspaces(token, sessionId);
      if (!ownsSession()) return;
      await writeCachedSessionWorkspaces(token, sessionId, workspaces);
      if (!ownsSession()) return;
      workspace.setSessionWorkspaces(sessionId, workspaces);
      const selectedWorkspaceId = workspaces.some(
        (item) => item.workspace_id === workspace.selectedWorkspaceId,
      )
        ? workspace.selectedWorkspaceId
        : (workspaces[0]?.workspace_id ?? null);
      const [blackboard, diagrams] = selectedWorkspaceId
        ? await Promise.all([
            loadAgentWorkspaceBlackboard(token, selectedWorkspaceId),
            loadAgentWorkspaceDiagramSummaries(token, selectedWorkspaceId),
          ])
        : [[], []];
      if (!ownsSession()) return;
      if (selectedWorkspaceId)
        await Promise.all([
          writeCachedWorkspaceBlackboard(
            token,
            selectedWorkspaceId,
            blackboard,
          ),
          writeCachedWorkspaceDiagrams(token, selectedWorkspaceId, diagrams),
        ]);
      if (ownsView())
        workspace.setWorkspaceBundle({
          workspaces,
          blackboard,
          diagrams,
          selectedWorkspaceId,
          sessionId,
        });
    } catch (error) {
      if (ownsView())
        ElMessage.warning(
          `加载工作区失败：${error instanceof Error ? error.message : "未知错误"}`,
        );
    } finally {
      if (showLoading && ownsView()) workspace.setWorkspaceLoading(false);
    }
  }

  async function selectWorkspace(token: string, workspaceId: number) {
    const account = captureAgentAccount(token);
    const sessionId = workspace.currentSessionId;
    const ownsSession = () =>
      account.isCurrent() &&
      (!sessionId || isAgentSessionCacheAvailable(token, sessionId));
    const sequence = ++viewSequence;
    const ownsView = () =>
      ownsSession() &&
      sequence === viewSequence &&
      workspace.currentSessionId === sessionId &&
      workspace.selectedWorkspaceId === workspaceId;
    if (!ownsSession()) return;
    workspace.setWorkspaceLoading(false);
    workspace.setSelectedWorkspace(workspaceId);
    const [cachedBlackboard, cachedDiagrams] = await Promise.all([
      readCachedWorkspaceBlackboard(token, workspaceId),
      readCachedWorkspaceDiagrams(token, workspaceId),
    ]);
    if (ownsView() && (cachedBlackboard || cachedDiagrams))
      workspace.setWorkspaceBundle({
        blackboard: cachedBlackboard?.data ?? [],
        diagrams: cachedDiagrams?.data ?? [],
        selectedWorkspaceId: workspaceId,
      });
    try {
      const [blackboard, diagrams] = await Promise.all([
        loadAgentWorkspaceBlackboard(token, workspaceId),
        loadAgentWorkspaceDiagramSummaries(token, workspaceId),
      ]);
      if (!ownsSession()) return;
      await Promise.all([
        writeCachedWorkspaceBlackboard(token, workspaceId, blackboard),
        writeCachedWorkspaceDiagrams(token, workspaceId, diagrams),
      ]);
      if (ownsView())
        workspace.setWorkspaceBundle({
          blackboard,
          diagrams,
          selectedWorkspaceId: workspaceId,
        });
    } catch (error) {
      if (ownsView())
        ElMessage.warning(
          `加载工作区失败：${error instanceof Error ? error.message : "未知错误"}`,
        );
    }
  }
  return { loadBundle, selectWorkspace };
}
