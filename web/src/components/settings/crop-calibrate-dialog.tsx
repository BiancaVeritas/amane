import {
  Alert,
  Box,
  Button,
  FileButton,
  Group,
  Image,
  Loader,
  Modal,
  Stack,
  Tabs,
  Text,
  TextInput,
} from "@mantine/core";
import { IconAlertCircle, IconPhoto, IconUpload } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { listMetadataOptions } from "@/client/@tanstack/react-query.gen";
import { ImageCropDialog } from "@/components/media/image-crop-dialog";
import { proxyImageUrl } from "@/lib/utils";

/** 标定结果: 相对封面宽高的比例 (0~1), 直接写进裁切规则. */
export interface CalibratedWindow {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface CropCalibrateDialogProps {
  opened: boolean;
  onClose: () => void;
  onCalibrated: (window: CalibratedWindow) => void;
}

/**
 * 从一张封面标定裁切窗口.
 *
 * 两种取图方式: 库里已有的影片封面 (复用它的封面地址), 或临时上传一张 —— 上传的图只在
 * 内存里当标定样本, 拿到比例后即丢弃, 不落库也不上传到服务端.
 */
export function CropCalibrateDialog({ opened, onClose, onCalibrated }: CropCalibrateDialogProps) {
  const { t } = useTranslation(["settings", "common"]);
  const [tab, setTab] = useState<string | null>("library");
  const [search, setSearch] = useState("");
  const [pendingSearch, setPendingSearch] = useState("");
  // 临时样本: objectURL 用完必须释放, 否则整页生命周期内都占着内存.
  const [uploadedUrl, setUploadedUrl] = useState<string | null>(null);
  const [sampleUrl, setSampleUrl] = useState<string | null>(null);
  const [calibrating, setCalibrating] = useState(false);

  const { data: picked, isLoading: listLoading } = useQuery({
    ...listMetadataOptions({ query: { search: pendingSearch || undefined, limit: 20 } }),
    enabled: opened && tab === "library",
  });

  useEffect(
    () => () => {
      if (uploadedUrl !== null) URL.revokeObjectURL(uploadedUrl);
    },
    [uploadedUrl],
  );

  const closeSample = () => {
    setSampleUrl(null);
    setCalibrating(false);
  };

  return (
    <>
      <Modal
        opened={opened && !calibrating}
        onClose={onClose}
        title={t("cropCalibrate.title")}
        size="xl"
        centered
      >
        <Stack gap="md">
          <Text size="sm" c="dimmed">
            {t("cropCalibrate.hint")}
          </Text>

          <Tabs value={tab} onChange={setTab}>
            <Tabs.List>
              <Tabs.Tab value="library" leftSection={<IconPhoto size={14} />}>
                {t("cropCalibrate.fromLibrary")}
              </Tabs.Tab>
              <Tabs.Tab value="upload" leftSection={<IconUpload size={14} />}>
                {t("cropCalibrate.fromUpload")}
              </Tabs.Tab>
            </Tabs.List>

            <Tabs.Panel value="library" pt="md">
              <Stack gap="sm">
                <Group>
                  <TextInput
                    flex={1}
                    placeholder={t("cropCalibrate.searchPlaceholder")}
                    value={search}
                    onChange={(e) => setSearch(e.currentTarget.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") setPendingSearch(search);
                    }}
                  />
                  <Button variant="light" onClick={() => setPendingSearch(search)}>
                    {t("cropCalibrate.search")}
                  </Button>
                </Group>
                {listLoading ? (
                  <Loader size="sm" />
                ) : (picked?.items ?? []).length === 0 ? (
                  <Text size="sm" c="dimmed">
                    {t("cropCalibrate.noFilm")}
                  </Text>
                ) : (
                  <SimpleFilmList
                    items={picked?.items ?? []}
                    onPick={(url) => {
                      setSampleUrl(url);
                      setCalibrating(true);
                    }}
                  />
                )}
              </Stack>
            </Tabs.Panel>

            <Tabs.Panel value="upload" pt="md">
              <Stack gap="sm">
                <Alert variant="light" color="blue" icon={<IconAlertCircle size={16} />}>
                  {t("cropCalibrate.uploadHint")}
                </Alert>
                <FileButton
                  accept="image/jpeg,image/png,image/webp"
                  onChange={(file) => {
                    if (file === null) return;
                    if (uploadedUrl !== null) URL.revokeObjectURL(uploadedUrl);
                    const url = URL.createObjectURL(file);
                    setUploadedUrl(url);
                    setSampleUrl(url);
                    setCalibrating(true);
                  }}
                >
                  {(props) => (
                    <Button {...props} leftSection={<IconUpload size={16} />}>
                      {t("cropCalibrate.pickFile")}
                    </Button>
                  )}
                </FileButton>
              </Stack>
            </Tabs.Panel>
          </Tabs>
        </Stack>
      </Modal>

      {calibrating && sampleUrl !== null && (
        <ImageCropDialog
          opened={calibrating}
          onClose={closeSample}
          imageUrl={sampleUrl}
          defaultAspect={0.7}
          defaultLockAspect={false}
          initialAnchor="right"
          submitting={false}
          labels={{
            title: t("cropCalibrate.cropTitle"),
            confirm: t("cropCalibrate.useWindow"),
            needSelection: t("cropCalibrate.needSelection"),
            operationFailed: t("common:toast.operationFailed"),
            aspect: t("cropCalibrate.aspect"),
            lockAspect: t("cropCalibrate.lockAspect"),
            left: t("cropCalibrate.left"),
            top: t("cropCalibrate.top"),
            width: t("cropCalibrate.width"),
            height: t("cropCalibrate.height"),
            cancel: t("common:actions.cancel"),
            rotateLeft: t("cropCalibrate.rotateLeft"),
            rotateRight: t("cropCalibrate.rotateRight"),
            resetRotation: t("cropCalibrate.resetRotation"),
          }}
          onSubmit={(box) => {
            // 自然像素框 → 相对比例: 规则按封面比例存放, 换分辨率也通用.
            void naturalToRatio(sampleUrl, box).then((ratio) => {
              onCalibrated(ratio);
              closeSample();
              onClose();
            });
          }}
        />
      )}
    </>
  );
}

/** 自然像素框 → 相对封面的比例 (0~1). */
async function naturalToRatio(
  url: string,
  box: { left: number; top: number; right: number; bottom: number },
): Promise<CalibratedWindow> {
  const size = await imageSize(url);
  const w = size.width || 1;
  const h = size.height || 1;
  return {
    left: round3(box.left / w),
    top: round3(box.top / h),
    width: round3((box.right - box.left) / w),
    height: round3((box.bottom - box.top) / h),
  };
}

function round3(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function imageSize(url: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    const img = new globalThis.Image();
    img.addEventListener(
      "load",
      () => resolve({ width: img.naturalWidth, height: img.naturalHeight }),
      {
        once: true,
      },
    );
    img.addEventListener("error", () => resolve({ width: 0, height: 0 }), { once: true });
    img.src = proxyImageUrl(url) ?? url;
  });
}

function SimpleFilmList({
  items,
  onPick,
}: {
  items: { id: number; number: string; title?: string | null; thumb_url?: string | null }[];
  onPick: (url: string) => void;
}) {
  const { t } = useTranslation("settings");
  return (
    <Box style={{ maxHeight: 420, overflowY: "auto" }}>
      <Stack gap={4}>
        {items.map((item) =>
          item.thumb_url ? (
            <Group key={item.id} wrap="nowrap" gap="sm">
              <Image
                src={proxyImageUrl(item.thumb_url)}
                alt=""
                h={54}
                w={96}
                fit="cover"
                radius="sm"
              />
              <Stack gap={0} style={{ flex: 1, minWidth: 0 }}>
                <Text size="sm" fw={500} lineClamp={1}>
                  {item.number}
                </Text>
                <Text size="xs" c="dimmed" lineClamp={1}>
                  {item.title ?? ""}
                </Text>
              </Stack>
              <Button
                size="compact-sm"
                variant="light"
                onClick={() => onPick(item.thumb_url as string)}
              >
                {t("cropCalibrate.pick")}
              </Button>
            </Group>
          ) : null,
        )}
      </Stack>
    </Box>
  );
}
