import { ActionIcon, Transition } from "@mantine/core";
import { IconArrowUp } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import classes from "./scroll-to-top.module.css";

/** 走多远才出现; 太早出现会挡住列表顶部的内容. */
const SHOW_AFTER_PX = 400;

/** 滚动容器上的标记; ``BrowsePageShell`` 在列表视图下挂它. */
const SCROLL_CONTAINER_SELECTOR = "[data-scroll-container]";

function resolveScroller(): HTMLElement | null {
  const marked = document.querySelector<HTMLElement>(SCROLL_CONTAINER_SELECTOR);
  if (marked !== null) return marked;
  return document.scrollingElement as HTMLElement | null;
}

/**
 * 列表页的「回到顶部」浮标.
 *
 * 两个页面在列表视图下不是窗口滚动, 而是 ``BrowsePageShell`` 自己的 ``overflowY: auto``
 * 容器; 卡片视图才回到文档流. 所以先找带标记的容器, 找不到才当成窗口滚动 —— 只盯一处的话
 * 总有一半场景看不到按钮.
 */
export function ScrollToTop() {
  const { t } = useTranslation("common");
  const scrollerRef = useRef<HTMLElement | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const target = resolveScroller();
    scrollerRef.current = target;
    const usesWindow = target === null || target === document.scrollingElement;
    const listenTarget: EventTarget = usesWindow ? window : (target as HTMLElement);

    const read = () => (usesWindow ? window.scrollY : (target as HTMLElement).scrollTop);
    // 滚动事件按帧触发, 每帧都 setState 会让整页跟着重渲染一次; 只在跨过阈值时改状态.
    const sync = () => {
      const next = read() > SHOW_AFTER_PX;
      setVisible((prev) => (prev === next ? prev : next));
    };
    listenTarget.addEventListener("scroll", sync, { passive: true });
    window.addEventListener("resize", sync, { passive: true });
    // 挂载后补一次: 从详情页返回列表时可能已经停在中间, 没有新的滚动事件.
    const id = requestAnimationFrame(sync);
    return () => {
      cancelAnimationFrame(id);
      listenTarget.removeEventListener("scroll", sync);
      window.removeEventListener("resize", sync);
    };
  }, []);

  const backToTop = () => {
    const target = scrollerRef.current;
    if (target === null || target === document.scrollingElement) {
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    target.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <Transition mounted={visible} transition="fade" duration={150}>
      {(style) => (
        <ActionIcon
          variant="filled"
          size={44}
          // 圆角方形: 圆形在满屏缩略图里太像「圆形头像」, 方块更像控件.
          radius="md"
          aria-label={t("actions.scrollToTop")}
          onClick={backToTop}
          // 平时半透明让内容透出来, 指上去才全亮.
          className={classes.fab}
          style={{
            ...style,
            position: "fixed",
            right: "1.5rem",
            bottom: "1.5rem",
            zIndex: 200,
          }}
        >
          <IconArrowUp size={22} />
        </ActionIcon>
      )}
    </Transition>
  );
}
