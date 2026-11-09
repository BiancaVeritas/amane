import { notifications } from "@mantine/notifications";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  adoptPosterMutation,
  adoptThumbMutation,
  uploadPosterMutation,
  uploadThumbMutation,
} from "@/client/@tanstack/react-query.gen";
import { extractErrorMessage } from "@/lib/api-error";

/** 单文件上限, 与服务端 ``MAX_UPLOAD_BYTES`` 一致; 前端先挡一次, 避免白传大文件. */
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

const ACCEPTED = ["image/jpeg", "image/png", "image/webp"] as const;

export type ImageTarget = "poster" | "thumb";

export interface UploadMetaImageOptions {
  metadataId: number;
  onSuccess?: () => void;
}

/**
 * 上传或采用封面 / 海报.
 *
 * 封面与海报是两套独立端点, 各写自己的字段: 上传什么就是什么, **都不裁剪** (想换构图走
 * 裁切对话框). 采用剧照走服务端下载, 因为站点图片不带 CORS 头, 前端 fetch 会被拦下.
 */
export function useUploadMetaImage({ metadataId, onSuccess }: UploadMetaImageOptions) {
  const { t } = useTranslation(["metadata", "common"]);
  const [pending, setPending] = useState<ImageTarget | null>(null);

  const notifyError = (err: unknown) => {
    notifications.show({
      message: extractErrorMessage(err, t("common:toast.operationFailed")),
      color: "red",
    });
  };

  const notifySuccess = () => {
    notifications.show({ message: t("detail.upload.success"), color: "blue" });
    onSuccess?.();
  };

  const uploadThumb = useMutation({
    ...uploadThumbMutation(),
    onSuccess: notifySuccess,
    onError: notifyError,
  });
  const uploadPoster = useMutation({
    ...uploadPosterMutation(),
    onSuccess: notifySuccess,
    onError: notifyError,
  });
  const adoptThumb = useMutation({
    ...adoptThumbMutation(),
    onSuccess: notifySuccess,
    onError: notifyError,
  });
  const adoptPoster = useMutation({
    ...adoptPosterMutation(),
    onSuccess: notifySuccess,
    onError: notifyError,
  });

  const validate = (file: File): boolean => {
    if (!ACCEPTED.includes(file.type as (typeof ACCEPTED)[number])) {
      notifications.show({ message: t("detail.upload.unsupported"), color: "red" });
      return false;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      notifications.show({ message: t("detail.upload.tooLarge"), color: "red" });
      return false;
    }
    return true;
  };

  const upload = (file: File, target: ImageTarget) => {
    if (!validate(file)) return;
    setPending(target);
    const options = {
      path: { metadata_id: metadataId },
      body: file,
      headers: { "Content-Type": "application/octet-stream" },
    };
    const done = { onSettled: () => setPending(null) };
    if (target === "thumb") uploadThumb.mutate(options, done);
    else uploadPoster.mutate(options, done);
  };

  const adopt = (sourceUrl: string, target: ImageTarget) => {
    if (!sourceUrl) return;
    setPending(target);
    const options = { path: { metadata_id: metadataId }, query: { source_url: sourceUrl } };
    const done = { onSettled: () => setPending(null) };
    if (target === "thumb") adoptThumb.mutate(options, done);
    else adoptPoster.mutate(options, done);
  };

  return { upload, adopt, pending };
}
