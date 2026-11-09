import { notifications } from "@mantine/notifications";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { uploadImageMutation } from "@/client/@tanstack/react-query.gen";
import { extractErrorMessage } from "@/lib/api-error";

/** 单文件上限, 与服务端 ``MAX_UPLOAD_BYTES`` 一致; 前端先挡一次, 避免白传大文件. */
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

const ACCEPTED = ["image/jpeg", "image/png", "image/webp"] as const;

export interface UploadMetaImageOptions {
  metadataId: number;
  onSuccess?: () => void;
}

/**
 * 上传自定义图片作为封面或海报.
 *
 * 海报会在服务端按当前比例自动裁一次, 用户不满意可再打开裁剪对话框调整; 封面保持原图.
 * 两种目标都不删除原有地址, 只把新图放到列表最前面.
 */
export function useUploadMetaImage({ metadataId, onSuccess }: UploadMetaImageOptions) {
  const { t } = useTranslation(["metadata", "common"]);

  const mutation = useMutation({
    ...uploadImageMutation(),
    onSuccess: () => {
      notifications.show({ message: t("detail.upload.success"), color: "blue" });
      onSuccess?.();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const upload = (file: File, target: "poster" | "thumb") => {
    if (!ACCEPTED.includes(file.type as (typeof ACCEPTED)[number])) {
      notifications.show({ message: t("detail.upload.unsupported"), color: "red" });
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      notifications.show({ message: t("detail.upload.tooLarge"), color: "red" });
      return;
    }
    void (async () => {
      mutation.mutate({
        path: { metadata_id: metadataId },
        query: { target },
        // 端点声明为 octet-stream, 直接送 File 就是原始字节.
        body: file,
        headers: { "Content-Type": "application/octet-stream" },
      });
    })();
  };

  return { upload, pending: mutation.isPending };
}
