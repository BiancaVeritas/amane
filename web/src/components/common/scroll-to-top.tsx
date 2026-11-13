import { ActionIcon, Transition } from "@mantine/core";
import { IconArrowUp } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import classes from "./scroll-to-top.module.css";

/** 走多远才出现; 太早出现会挡住列表顶部的内容. */
const SHOW_AFTER_PX = 400;

/** 滚动容器上的标记; ``BrowsePageShell`` 在列表视图下挂它. */
const SCROLL_CONTAINER_SELECTOR = "[data-scroll-container]";

/**
 * 找出真正在滚的元素.
 *
 * 标记容器本身往往被内容撑满 (``scrollHeight === clientHeight``), 实际溢出的是它**内部**
 * 包住表格的那一层; 卡片视图则滚整个文档. 因此先往下找一个内容高于可视高度的容器,
 * 找不到才退回文档滚动.
 */
function resolveScroller(): HTMLElement | null {
  const root = document.querySelector<HTMLElement>(SCROLL_CONTAINER_SELECTOR);
  if (root !== null) {
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
  }
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
    // 捕获阶段监听: scroll 不冒泡, 但捕获能收到任意元素的滚动, 因此不必事先确定容器是谁.
    // 真正的滚动目标可能是标记容器的子层 (表格外包的那层), 挂载时还不一定渲染出来.
    const onScroll = (event: Event) => {
      const target = event.target;
      const value =
        target === document || target === window
          ? window.scrollY
          : target instanceof HTMLElement
            ? target.scrollTop
            : 0;
      // 每帧都 setState 会让整页重渲染; 只在跨过阈值时改状态.
      const next = value > SHOW_AFTER_PX;
      setVisible((prev) => (prev === next ? prev : next));
      if (value > SHOW_AFTER_PX && target instanceof HTMLElement) scrollerRef.current = target;
    };
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });
    const onResize = () => {
      const target = resolveScroller();
      scrollerRef.current = target;
      const value = target?.scrollTop ?? window.scrollY;
      setVisible(value > SHOW_AFTER_PX);
    };
    window.addEventListener("resize", onResize, { passive: true });
    // 挂载后补一次: 从详情页返回列表时可能已经停在中间, 没有新的滚动事件.
    const id = requestAnimationFrame(onResize);
    return () => {
      cancelAnimationFrame(id);
      document.removeEventListener("scroll", onScroll, { capture: true });
      window.removeEventListener("resize", onResize);
    };
  }, []);

  const backToTop = () => {
    const target = scrollerRef.current ?? resolveScroller();
    if (target === null || target === document.scrollingElement) {
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    target.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <Transition mounted={visible} transition="fade" duration={150}>
      {(style) => (
        // 外层只承载 Transition 的淡入动画 (它自带 opacity), 半透明状态放在内层按钮上,
        // 两者相乘: 进场时淡入, 静止时半透明, 悬停全亮.
        <div
          style={{
            ...style,
            position: "fixed",
            right: "clamp(0.75rem, 2vw, 1.5rem)",
            bottom: "clamp(0.75rem, 2vw, 1.5rem)",
            zIndex: 200,
            lineHeight: 0,
          }}
        >
          <ActionIcon
            variant="filled"
            // 圆角方形: 圆形在满屏缩略图里太像「圆形头像」, 方块更像控件.
            radius="md"
            aria-label={t("actions.scrollToTop")}
            onClick={backToTop}
            // 平时半透明让内容透出来, 指上去才全亮.
            className={classes.fab}
            style={{
              // 随视口自适应, 边界按需求定死: 最小 45px, 最大 75px.
              width: "clamp(2.8125rem, 4vw, 4.6875rem)",
              height: "clamp(2.8125rem, 4vw, 4.6875rem)",
              minWidth: 0,
              minHeight: 0,
            }}
          >
            <IconArrowUp style={{ width: "55%", height: "55%" }} stroke={2} />
          </ActionIcon>
        </div>
      )}
    </Transition>
  );
}
