import { ActionIcon, Transition } from "@mantine/core";
import { useWindowScroll } from "@mantine/hooks";
import { IconArrowUp } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";

/** 走多远才出现; 太早出现会挡住列表顶部的内容. */
const SHOW_AFTER_PX = 400;

/**
 * 列表页的「回到顶部」浮标.
 *
 * 挂在窗口滚动上: 这两个页面自己不做内部滚动, 用的是文档流.
 */
export function ScrollToTop() {
  const { t } = useTranslation("common");
  const [scroll, scrollTo] = useWindowScroll();
  const visible = scroll.y > SHOW_AFTER_PX;

  return (
    <Transition mounted={visible} transition="fade" duration={150}>
      {(style) => (
        <ActionIcon
          variant="filled"
          size="lg"
          radius="xl"
          aria-label={t("actions.scrollToTop")}
          onClick={() => scrollTo({ y: 0 })}
          style={{
            ...style,
            position: "fixed",
            right: "1.5rem",
            bottom: "1.5rem",
            zIndex: 200,
          }}
        >
          <IconArrowUp size={20} />
        </ActionIcon>
      )}
    </Transition>
  );
}
