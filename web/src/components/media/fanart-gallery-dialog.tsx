import { Box, Image as MantineImage, Modal, SimpleGrid, Text } from "@mantine/core";
import { useState } from "react";
import { proxyImageUrl } from "@/lib/utils";
import { Z_INDEX } from "@/lib/z-index";
import { FanartLightbox } from "./fanart-lightbox";

interface FanartGalleryDialogProps {
  opened: boolean;
  onClose: () => void;
  images: string[];
  title: string;
}

/**
 * 剧照总览弹窗: 网格铺开全部剧照, 点某一张打开大图.
 *
 * 详情页的缩略条只渲染前几张 (每张外链都要经反代现场下载), 其余的用「+N」进到这里看.
 *
 * 一格一格的缩略图**不预先全部请求**也没有意义: 网格本身就是「看全部」, 所以这里按
 * 视口惰性加载 —— 用 ``loading="lazy"`` 而不是 ProxyImage 的队列, 避免退出弹窗后槽位
 * 被已卸载的图占着.
 */
export function FanartGalleryDialog({ opened, onClose, images, title }: FanartGalleryDialogProps) {
  const [previewAt, setPreviewAt] = useState<number | null>(null);

  return (
    <>
      <Modal
        opened={opened}
        onClose={onClose}
        title={`${title} (${images.length})`}
        size="xl"
        centered
        zIndex={Z_INDEX.imagePicker}
        // 大图预览要压在这个弹窗之上, 所以不能让它随网格一起卸载.
        styles={{ body: { maxHeight: "75vh", overflowY: "auto" } }}
      >
        <SimpleGrid cols={{ base: 3, sm: 4, md: 5 }} spacing="xs">
          {images.map((url, index) => (
            <Box
              key={`${url}-${String(index)}`}
              component="button"
              type="button"
              onClick={() => setPreviewAt(index)}
              style={{
                padding: 0,
                border: "1px solid var(--mantine-color-default-border)",
                borderRadius: "var(--mantine-radius-sm)",
                overflow: "hidden",
                cursor: "zoom-in",
                background: "none",
                lineHeight: 0,
              }}
            >
              <MantineImage
                src={proxyImageUrl(url)}
                alt=""
                h={110}
                fit="cover"
                loading="lazy"
                w="100%"
              />
            </Box>
          ))}
        </SimpleGrid>
        {images.length === 0 && (
          <Text size="sm" c="dimmed" ta="center" py="lg">
            —
          </Text>
        )}
      </Modal>
      {previewAt !== null && (
        <FanartLightbox
          images={images}
          initialIndex={previewAt}
          onClose={() => setPreviewAt(null)}
        />
      )}
    </>
  );
}
