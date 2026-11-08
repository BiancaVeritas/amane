/**
 * 复制文本到剪贴板.
 *
 * `navigator.clipboard` 只在安全上下文 (HTTPS 或 localhost) 里存在; 用 HTTP + IP 访问自建
 * 服务时它是 undefined. 这种情况下退回 `document.execCommand("copy")` —— 该接口已废弃但
 * 在各浏览器里仍可用, 且不要求安全上下文.
 *
 * 返回是否成功, 调用方据此提示.
 */
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // 权限被拒或页面失焦, 继续尝试旧接口.
    }
  }
  return legacyCopy(text);
}

/** 选中一个临时 textarea 再执行 copy 命令; 不改变用户当前的选区与焦点. */
function legacyCopy(text: string): boolean {
  const active = document.activeElement;
  const area = document.createElement("textarea");
  area.value = text;
  // 固定定位并移出视口, 避免触发页面滚动.
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "-1000px";
  area.style.opacity = "0";
  document.body.appendChild(area);
  try {
    area.select();
    area.setSelectionRange(0, text.length);
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    document.body.removeChild(area);
    // 恢复焦点, 否则键盘操作会从 body 重新开始.
    if (active instanceof HTMLElement) {
      active.focus();
    }
  }
}
