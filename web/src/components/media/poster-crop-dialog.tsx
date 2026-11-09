import { Select } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { cropPosterFromThumbMutation } from "@/client/@tanstack/react-query.gen";
import type { ContentType } from "@/client/types.gen";
import { ImageCropDialog } from "@/components/media/image-crop-dialog";
import { useConfig } from "@/hooks/use-config";
import { extractErrorMessage } from "@/lib/api-error";

const DEFAULT_POSTER_RATIO = 0.7;

export interface PosterCropDialogProps {
  opened: boolean;
  onClose: () => void;
  metadataId: number;
  thumbUrl: string;
  contentType: ContentType;
  /** 其它可用作裁切基准的图 (海报、剧照); 提供时对话框内可切换. */
  alternativeSources?: string[];
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
    <ImageCropDialog
      opened={opened}
      onClose={onClose}
      imageUrl={activeSource}
      defaultAspect={configRatio}
      initialAnchor="right"
      submitting={mutation.isPending}
      headerExtra={
        sources.length > 1 ? (
          <Select
            size="xs"
            data={sources.map((_url, index) => ({
              value: String(index),
              label:
                index === 0
                  ? t("detail.cropPoster.sourceCover")
                  : t("detail.cropPoster.sourceOther", { index }),
            }))}
            value={String(sourceIndex)}
            onChange={(value) => setSourceIndex(Number(value ?? 0))}
            allowDeselect={false}
            w={160}
          />
        ) : undefined
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
  );
}
