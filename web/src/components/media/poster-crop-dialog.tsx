import { Button, Group } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCrop, IconPhoto } from "@tabler/icons-react";
import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { cropPosterFromThumbMutation } from "@/client/@tanstack/react-query.gen";
import type { ContentType } from "@/client/types.gen";
import { ImageCropDialog } from "@/components/media/image-crop-dialog";
import { type ImageInfoItem, ImagePickerDialog } from "@/components/media/image-picker-dialog";
import { useConfig } from "@/hooks/use-config";
import { extractErrorMessage } from "@/lib/api-error";

const DEFAULT_POSTER_RATIO = 0.7;

/** 比例保留三位小数, 与规则存储精度一致. */
function round3(value: number): number {
  return Math.round(value * 1000) / 1000;
}

export interface PosterCropDialogProps {
  opened: boolean;
  onClose: () => void;
  metadataId: number;
  thumbUrl: string;
  contentType: ContentType;
  /** 其它可用作裁切基准的图 (海报、剧照); 提供时对话框内可切换. */
  alternativeSources?: string[];
  /** 每张候选的来源与分辨率, 交给选图框显示; 与详情页换图共用同一份. */
  infos?: Map<string, ImageInfoItem>;
  /** 把当前裁切框交给「编辑裁切规则」弹框, 由调用方负责插入规则. */
  onAddRule?: (window: { left: number; top: number; width: number; height: number }) => void;
  onSuccess?: () => void;
}

/** 影片海报裁切: 基准 thumb_urls[0] 当前本地文件像素, 结果替换 poster_urls. */
export function PosterCropDialog({
  opened,
  onClose,
  metadataId,
  thumbUrl,
  contentType,
  alternativeSources = [],
  infos,
  onAddRule,
  onSuccess,
}: PosterCropDialogProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const { data: config } = useConfig();
  const configRatio =
    (contentType === "uncensored"
      ? config?.scraping?.uncensored_poster_ratio
      : config?.scraping?.poster_ratio) ?? DEFAULT_POSTER_RATIO;

  // 可选基准图: 当前封面在最前, 其后是海报与剧照; 切换只影响本次裁切的基准.
  const sources = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const url of [thumbUrl, ...alternativeSources]) {
      if (!url || seen.has(url)) continue;
      seen.add(url);
      out.push(url);
    }
    return out;
  }, [thumbUrl, alternativeSources]);
  const [sourceIndex, setSourceIndex] = useState(0);
  // 换图或重新打开时把下标归零: 在渲染期派生, 避免 effect 里同步 setState 触发额外一轮渲染.
  const [prevKey, setPrevKey] = useState(`${String(opened)}:${thumbUrl}`);
  const currentKey = `${String(opened)}:${thumbUrl}`;
  if (currentKey !== prevKey) {
    setPrevKey(currentKey);
    setSourceIndex(0);
  }
  const activeSource = sources[Math.min(sourceIndex, sources.length - 1)] ?? thumbUrl;
  const [pickerOpen, setPickerOpen] = useState(false);
  // 当前裁切框 (自然像素) 与基准图尺寸: 把像素换成比例才能存进规则.
  const [selection, setSelection] = useState<{
    left: number;
    top: number;
    width: number;
    height: number;
  } | null>(null);
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);

  /** 当前裁切框 → 规则用的 0~1 比例; 尺寸未知时返回 null. */
  const selectionRatio = () => {
    if (selection === null || naturalSize === null) return null;
    const w = naturalSize.width || 1;
    const h = naturalSize.height || 1;
    return {
      left: round3(selection.left / w),
      top: round3(selection.top / h),
      width: round3(selection.width / w),
      height: round3(selection.height / h),
    };
  };

  const addRuleFromCrop = () => {
    const win = selectionRatio();
    if (win === null) {
      notifications.show({ message: t("detail.cropPoster.needSelection"), color: "red" });
      return;
    }
    onAddRule?.(win);
  };

  const mutation = useMutation({
    ...cropPosterFromThumbMutation(),
    onSuccess: () => {
      notifications.show({
        message: t("detail.cropPoster.success"),
        color: "blue",
      });
      onSuccess?.();
      onClose();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  return (
    <>
      {/* 选图框压在裁切框之上 (zIndex), 裁切框保持打开 —— 选完新基准继续裁. */}
      <ImageCropDialog
        opened={opened}
        onClose={onClose}
        imageUrl={activeSource}
        defaultAspect={configRatio}
        initialAnchor="right"
        submitting={mutation.isPending}
        onSelectionChange={(box) => {
          setSelection({
            left: box.left,
            top: box.top,
            width: box.right - box.left,
            height: box.bottom - box.top,
          });
        }}
        onNaturalSizeChange={setNaturalSize}
        headerExtra={
          <Group gap={6} wrap="nowrap">
            {/* 只有一张图也保留入口: 将来换源时不必先想到「这里原本有没有按钮」. */}
            <Button
              size="compact-xs"
              variant="light"
              leftSection={<IconPhoto size={12} />}
              onClick={() => setPickerOpen(true)}
            >
              {t("detail.cropPoster.selectSource")}
            </Button>
            {onAddRule ? (
              <Button
                size="compact-xs"
                variant="light"
                leftSection={<IconCrop size={12} />}
                onClick={() => addRuleFromCrop()}
              >
                {t("detail.cropPoster.addRule")}
              </Button>
            ) : null}
          </Group>
        }
        onSubmit={(box, rotation) =>
          mutation.mutate({
            path: { metadata_id: metadataId },
            body: { ...box, rotation, source_url: activeSource === thumbUrl ? null : activeSource },
          })
        }
        labels={{
          title: t("detail.cropPoster.title"),
          confirm: t("detail.cropPoster.confirm"),
          needSelection: t("detail.cropPoster.needSelection"),
          operationFailed: t("common:toast.operationFailed"),
          aspect: t("detail.cropPoster.aspect"),
          lockAspect: t("detail.cropPoster.lockAspect"),
          left: t("detail.cropPoster.left"),
          top: t("detail.cropPoster.top"),
          width: t("detail.cropPoster.width"),
          height: t("detail.cropPoster.height"),
          cancel: t("common:actions.cancel"),
          rotateLeft: t("detail.cropPoster.rotateLeft"),
          rotateRight: t("detail.cropPoster.rotateRight"),
          resetRotation: t("detail.cropPoster.resetRotation"),
        }}
      />
      {/* 选图完成后关闭自身, 裁剪框随即重新打开并换成新基准. */}
      <ImagePickerDialog
        opened={pickerOpen}
        onClose={() => setPickerOpen(false)}
        images={sources}
        infos={infos}
        title={t("detail.cropPoster.selectSource")}
        onPick={(url) => {
          setSourceIndex(Math.max(0, sources.indexOf(url)));
          setPickerOpen(false);
        }}
      />
    </>
  );
}
