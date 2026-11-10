import { Portal } from "@mantine/core";
import { useEffect, useRef } from "react";
import type { Crop } from "react-image-crop";

interface CropMagnifierProps {
  /** 画布里的原图 (含当前旋转). */
  source: HTMLCanvasElement | null;
  /** 当前选区; 用来算拖动点和画虚线. */
  crop: Crop | undefined;
  /** 对话框是否打开. */
  enabled: boolean;
  /** 放大倍率. */
  zoom?: number;
  /** 镜片边长 (CSS px). */
  size?: number;
}

/** 选框本体与四角/四边的把手都在这个节点内. */
const SELECTION_SELECTOR = ".ReactCrop__crop-selection";

/**
 * 拖动选框时跟随的方形放大镜.
 *
 * 只在**按住选框**时出现: 点图片其它地方是重新框选, 不该弹镜头. 取景以指针为中心,
 * 靠近画布边缘时整体内移 —— 宁可中心偏移, 也不要露出画布外的黑边. 镜头里画出选区虚线,
 * 便于对齐正在调整的那条边.
 *
 * 像素从画布取而非重新加载图片: 画布上已是旋转后的正确朝向, 取它才不会和实际裁出的结果
 * 对不上.
 */
export function CropMagnifier({ source, crop, enabled, zoom = 2, size = 132 }: CropMagnifierProps) {
  const lensRef = useRef<HTMLCanvasElement | null>(null);
  const holderRef = useRef<HTMLDivElement | null>(null);
  // 「是否按住左键」用显式状态记, 不读 style.opacity —— 那是渲染期才写进 DOM 的.
  const pressedRef = useRef(false);

  useEffect(() => {
    if (!enabled) return;

    const hide = () => {
      pressedRef.current = false;
      const holder = holderRef.current;
      if (holder !== null) holder.style.opacity = "0";
    };

    /** 当前选区在画布像素坐标系里的矩形. */
    const selectionRect = (): { x: number; y: number; w: number; h: number } | null => {
      if (source === null || crop === undefined) return null;
      const shownW = source.clientWidth || 1;
      const shownH = source.clientHeight || 1;
      const scaleX = source.width / shownW;
      const scaleY = source.height / shownH;
      if (crop.unit === "px") {
        return {
          x: crop.x * scaleX,
          y: crop.y * scaleY,
          w: crop.width * scaleX,
          h: crop.height * scaleY,
        };
      }
      return {
        x: (crop.x / 100) * source.width,
        y: (crop.y / 100) * source.height,
        w: (crop.width / 100) * source.width,
        h: (crop.height / 100) * source.height,
      };
    };

    /** 鼠标位置 → 画布像素. */
    const toCanvas = (clientX: number, clientY: number) => {
      if (source === null) return null;
      const rect = source.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return null;
      return {
        x: ((clientX - rect.left) / rect.width) * source.width,
        y: ((clientY - rect.top) / rect.height) * source.height,
      };
    };

    /** 镜片贴在指针右下方; 靠近视口边缘时翻到另一侧. */
    const place = (clientX: number, clientY: number) => {
      const holder = holderRef.current;
      if (holder === null) return;
      // 默认摆在指针右下方 24px, 留出距离才看得见光标与它下面的选框边;
      // 那一侧放不下就整块翻到另一侧, 而不是硬夹回来盖住光标.
      const gap = 24;
      const left =
        clientX + gap + size <= window.innerWidth
          ? clientX + gap
          : Math.max(0, clientX - gap - size);
      const top =
        clientY + gap + size <= window.innerHeight
          ? clientY + gap
          : Math.max(0, clientY - gap - size);
      holder.style.transform = `translate3d(${left}px, ${top}px, 0)`;
      holder.style.opacity = "1";
    };

    /** 画一帧镜片内容: 以指针为中心的放大画面 + 选区虚线. */
    const render = (clientX: number, clientY: number) => {
      const lens = lensRef.current;
      if (lens === null || source === null) return;
      const ctx = lens.getContext("2d");
      if (ctx === null) return;
      const pointer = toCanvas(clientX, clientY);
      if (pointer === null) return;

      const ratio = size / zoom;
      const half = ratio / 2;
      // 以指针为中心取景, 但把取景框钳在画布内: 靠近边缘时宁可中心偏移, 也不要露出黑色空白.
      const originX = Math.max(0, Math.min(pointer.x - half, Math.max(0, source.width - ratio)));
      const originY = Math.max(0, Math.min(pointer.y - half, Math.max(0, source.height - ratio)));

      ctx.imageSmoothingEnabled = false;
      ctx.clearRect(0, 0, size, size);
      ctx.drawImage(source, originX, originY, ratio, ratio, 0, 0, size, size);

      // 选区四边: 便于对齐正在调的那条边.
      //
      // 先描一道黑色实线打底, 再叠白色虚线: 单用白色落在亮部会整个消失, 单用黑色落在暗部
      // 又会看不清. 两层叠起来在任意底色上都有对比, 这是图像软件里选区边框的通行做法.
      const sel = selectionRect();
      if (sel !== null) {
        const scale = size / ratio;
        const edges = [
          { from: [sel.x, sel.y], to: [sel.x + sel.w, sel.y] },
          { from: [sel.x, sel.y + sel.h], to: [sel.x + sel.w, sel.y + sel.h] },
          { from: [sel.x, sel.y], to: [sel.x, sel.y + sel.h] },
          { from: [sel.x + sel.w, sel.y], to: [sel.x + sel.w, sel.y + sel.h] },
        ];
        const trace = () => {
          ctx.beginPath();
          for (const edge of edges) {
            ctx.moveTo((edge.from[0] - originX) * scale, (edge.from[1] - originY) * scale);
            ctx.lineTo((edge.to[0] - originX) * scale, (edge.to[1] - originY) * scale);
          }
        };

        ctx.save();
        // 底层: 黑实线, 稍粗一点, 让白虚线落下时有明确轮廓.
        ctx.setLineDash([]);
        ctx.lineWidth = 2;
        ctx.strokeStyle = "rgba(0, 0, 0, 0.9)";
        trace();
        ctx.stroke();

        // 上层: 白虚线.
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.95)";
        trace();
        ctx.stroke();
        ctx.restore();
      }
    };

    const onDown = (e: PointerEvent | MouseEvent) => {
      if (e.button !== 0) return;
      // 只有按在选框上才出镜; 按图片其它地方是重新框选, 不弹镜头.
      const target = e.target;
      if (!(target instanceof Element) || target.closest(SELECTION_SELECTOR) === null) return;
      pressedRef.current = true;
      place(e.clientX, e.clientY);
      render(e.clientX, e.clientY);
    };

    const onMove = (e: PointerEvent | MouseEvent) => {
      if (!pressedRef.current) return;
      place(e.clientX, e.clientY);
      render(e.clientX, e.clientY);
    };

    // pointer 与 mouse 都挂: ReactCrop 走 pointer 事件, 拖动时 mouse 事件在部分浏览器里
    // 不完整; 两套都听, 重复触发只是重画同一帧.
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("pointermove", onMove, true);
    document.addEventListener("pointerup", hide, true);
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("mousemove", onMove, true);
    document.addEventListener("mouseup", hide, true);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("pointermove", onMove, true);
      document.removeEventListener("pointerup", hide, true);
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("mousemove", onMove, true);
      document.removeEventListener("mouseup", hide, true);
    };
  }, [enabled, source, crop, zoom, size]);

  if (!enabled) return null;

  return (
    // Portal 到 body: 弹框祖先带 transform 时会劫持 position: fixed 的定位, 镜片会跟着
    // 弹框缩放而偏移, 还会被弹框的 overflow 裁掉. 挂到 body 才能始终贴着视口走.
    <Portal>
      <div
        ref={holderRef}
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          width: size,
          height: size,
          // 位置只由 transform 决定; 初值放到视口外, 免得闪现到左上角.
          transform: "translate3d(-9999px, -9999px, 0)",
          opacity: 0,
          transition: "opacity 100ms",
          pointerEvents: "none",
          zIndex: 10000,
          border: "2px solid var(--mantine-color-body)",
          boxShadow: "0 4px 16px rgba(0, 0, 0, 0.45)",
          background: "var(--mantine-color-body)",
        }}
      >
        <canvas ref={lensRef} width={size} height={size} style={{ display: "block" }} />
      </div>
    </Portal>
  );
}
