import { Button, Group, Input, Modal, Stack } from "@mantine/core";
import { IconFolder } from "@tabler/icons-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FileBrowser } from "./file-browser";

interface PathPickerProps {
  /** Current path value. */
  value: string;
  /** Callback when path changes. */
  onChange: (value: string) => void;
  /** What to pick: file, directory, or mixed. */
  pathType?: "file" | "directory" | "mixed";
  /** Initial directory to browse from. */
  initialPath?: string;
  /** Label for the input. */
  label?: string;
  /** Whether the input is read-only. */
  disabled?: boolean;
  /** Placeholder text. */
  placeholder?: string;
}

export function PathPicker({
  value,
  onChange,
  pathType = "directory",
  initialPath,
  label,
  disabled = false,
  placeholder = "/path/to/directory",
}: PathPickerProps) {
  const { t } = useTranslation("common");
  const [dialogOpen, setDialogOpen] = useState(false);

  const dialogTitle =
    pathType === "file"
      ? t("actions.selectFile", { defaultValue: "Select File" })
      : pathType === "directory"
        ? t("actions.selectDirectory", { defaultValue: "Select Directory" })
        : t("actions.selectPath", { defaultValue: "Select Path" });

  const inputId = label ? `path-picker-${label}` : undefined;

  return (
    <Stack gap={6}>
      {label && (
        <Input.Label htmlFor={inputId} size="sm" fw={500}>
          {label}
        </Input.Label>
      )}
      <Group gap="xs" wrap="nowrap">
        <Input
          id={inputId}
          value={value}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          style={{ flex: 1 }}
        />
        {!disabled && (
          <Button
            type="button"
            variant="outline"
            leftSection={<IconFolder size={16} />}
            onClick={() => setDialogOpen(true)}
          >
            {t("actions.browse", { defaultValue: "Browse" })}
          </Button>
        )}
      </Group>

      {!disabled && (
        <Modal
          opened={dialogOpen}
          onClose={() => setDialogOpen(false)}
          title={dialogTitle}
          size="90vw"
          styles={{ content: { maxWidth: 1280 } }}
          centered
        >
          <FileBrowser
            initialPath={value || initialPath || "__roots__"}
            selectionType={pathType}
            allowMultiple={false}
            showPathInput={true}
            onSelect={(paths) => {
              if (paths.length > 0) {
                onChange(paths[0]);
              }
              setDialogOpen(false);
            }}
          />
          <Group justify="flex-end" mt="md">
            <Button variant="subtle" size="sm" onClick={() => setDialogOpen(false)}>
              {t("actions.cancel")}
            </Button>
          </Group>
        </Modal>
      )}
    </Stack>
  );
}
