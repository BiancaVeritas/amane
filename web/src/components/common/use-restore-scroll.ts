import { useEffect } from "react";

/** 位置存 sessionStorage: 组件卸载/重新挂载后仍在, 关掉标签页才清. */
const PREFIX = "amane:scroll:";

/**
 * 只记一个纵向位置.
 *
 * 列表视图与卡片视图的滚动容器**不是同一个**, 而且不一定是哪个: 列表模式下
 * ``BrowsePageShell`` 的容器有固定高度, 真正滚动的往往是它**内部**包住表格的那层;
 * 卡片模式则整个文档滚. 与其按元素标识去记 (那要求容器有 id 或标记), 不如只记住
 * 「这条列表上次滚到了多少」, 恢复时把当前真正能滚的那个元素滚到该位置.
 */
function storageGet(key: string): number | undefined {
  try {
    const raw = sessionStorage.getItem(PREFIX + key);
    return raw === null ? undefined : Number(raw);
  } catch {
    return undefined;
  }
}

function storageSet(key: string, value: number): void {
  try {
    sessionStorage.setItem(PREFIX + key, String(value));
  } catch {
    // 存储不可用 (隐私模式/配额) 时放弃记忆, 不影响其他功能.
  }
}

/** 读取事件目标当前的纵向滚动量. */
function readScroll(target: EventTarget | null): number {
  if (target === document || target === window) return window.scrollY;
  return target instanceof HTMLElement ? target.scrollTop : 0;
}

/**
 * 找出当前真正在滚的元素: 从页面根往下找第一个「内容高于可视高度」的容器.
 *
 * 深度优先且优先内层, 因为外层容器为了容纳内容通常被撑满, ``scrollHeight === clientHeight``,
 * 真正溢出的是内层那个。
 */
function findScroller(): HTMLElement | null {
  const root =
    document.querySelector<HTMLElement>("[data-scroll-container]") ??
    document.querySelector<HTMLElement>("main") ??
    document.body;

  const walk = (el: HTMLElement): HTMLElement | null => {
    for (const child of el.children) {
      if (!(child instanceof HTMLElement)) continue;
      const found = walk(child);
      if (found !== null) return found;
    }
    const style = getComputedStyle(el);
    const scrollable = style.overflowY === "auto" || style.overflowY === "scroll";
    return scrollable && el.scrollHeight > el.clientHeight + 1 ? el : null;
  };

  const inner = walk(root);
  if (inner !== null) return inner;
  // 内层都没有可滚的, 再看文档本身.
  const doc = document.scrollingElement;
  return doc instanceof HTMLElement && doc.scrollHeight > doc.clientHeight + 1 ? doc : null;
}

/**
 * 记住并恢复列表页的滚动位置.
 *
 * 路由自带的 ``scrollRestoration`` 只在前后路由不同时恢复, 而「切到设置再切回来」的前后
 * 地址完全相同, 因此它不会做任何事. 这里按 key 自己记.
 *
 * 用捕获阶段监听: scroll 事件不冒泡, 但捕获阶段能拿到任意元素的滚动, 于是无需事先知道
 * 真正的容器是谁. 恢复时再找到当前可滚的那个元素写回去, 并在内容异步撑高后重试几轮.
 */
export function useRestoreScroll(key: string): void {
  useEffect(() => {
    const saved = storageGet(key);

    const onScroll = (event: Event) => {
      const value = readScroll(event.target);
      if (value > 0) storageSet(key, value);
    };
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });

    // 内容异步到达, 高度会陆续变大; 写一次可能被截断, 因此多点重试.
    const timers: number[] = [];
    const restore = () => {
      if (saved === undefined || saved <= 0) return;
      const el = findScroller();
      if (el === null) return;
      // 只在还没到目标位置时补写, 免得把用户这期间的主动滚动拽回去.
      if (el.scrollTop < saved) el.scrollTop = saved;
      else if (window.scrollY < saved) window.scrollTo({ top: saved, behavior: "instant" });
    };
    for (const delay of [0, 16, 50, 120, 250, 500, 900, 1500]) {
      timers.push(window.setTimeout(restore, delay));
    }

    return () => {
      for (const id of timers) window.clearTimeout(id);
      document.removeEventListener("scroll", onScroll, { capture: true });
    };
  }, [key]);
}
