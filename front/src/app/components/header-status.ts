export function formatConnectionStatusLabel(label: string, connected: boolean): string {
  return `${label}${connected ? "已连通" : "断开连接"}`;
}
