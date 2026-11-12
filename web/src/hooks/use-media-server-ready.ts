import { useConfig } from "@/hooks/use-config";

/**
 * 媒体服务器是否可推送人物.
 *
 * 与后端 ``MediaServerConfig.enabled`` 同一口径: url 与 api_key 都填了才算可用.
 * 返回 ``ready`` 给按钮做禁用判定, ``reason`` 给悬停提示用.
 */
export function useMediaServerReady(): { ready: boolean; reason: string } {
  const { data: config } = useConfig();
  const server = config?.media_server;
  const ready = Boolean(server?.url && server?.api_key);
  return {
    ready,
    reason: ready ? "" : "请在 媒体服务器 填写 API Key",
  };
}
