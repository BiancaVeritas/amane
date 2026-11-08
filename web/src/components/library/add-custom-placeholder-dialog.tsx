import { Button, Group, Modal, Stack, Text, TextInput } from "@mantine/core";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

interface AddCustomPlaceholderDialogProps {
  opened: boolean;
  onClose: () => void;
  existing: Record<string, string>;
  onSubmit: (name: string, literal: string) => void;
}

/** 占位符名要能写进 `{...}`, 因此限合法标识符; 首字符不能是数字. */
const NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;

/**
 * 新增自定义占位符.
 *
 * 名字是模板里 `{名字}` 引用的键; 匹配串是原始文件名里要找的字面串, 命中后占位符取
 * 该串的大写形式. 两者分开填, 因为文件名里的标记写法可能与小写命名不一致.
 */
export function AddCustomPlaceholderDialog({
  opened,
  onClose,
  existing,
  onSubmit,
}: AddCustomPlaceholderDialogProps) {
  const { t } = useTranslation("library");
  const { t: tCommon } = useTranslation("common");
  const [name, setName] = useState("");
  const [literal, setLiteral] = useState("");
  const [touched, setTouched] = useState(false);

  const reset = useCallback(() => {
    setName("");
    setLiteral("");
    setTouched(false);
  }, []);

  const trimmedName = name.trim();
  const trimmedLiteral = literal.trim();
  const nameError =
    !trimmedName || !NAME_PATTERN.test(trimmedName)
      ? t("placeholders.customNameInvalid")
      : trimmedName in existing
        ? t("placeholders.customNameExists")
        : null;
  const literalError = trimmedLiteral ? null : t("placeholders.customLiteralRequired");
  const valid = nameError == null && literalError == null;

  const submit = () => {
    setTouched(true);
    if (!valid) {
      return;
    }
    onSubmit(trimmedName, trimmedLiteral);
  };

  const close = () => {
    reset();
    onClose();
  };

  return (
    <Modal opened={opened} onClose={close} title={t("placeholders.addCustom")} centered>
      <Stack gap="sm">
        <Text size="xs" c="dimmed">
          {t("placeholders.addCustomHint")}
        </Text>
        <TextInput
          label={t("placeholders.customName")}
          description={t("placeholders.customNameHint")}
          placeholder="LTX"
          value={name}
          error={touched ? nameError : null}
          onChange={(event) => setName(event.currentTarget.value)}
        />
        <TextInput
          label={t("placeholders.customLiteral")}
          description={t("placeholders.customLiteralHint")}
          placeholder="LTX"
          value={literal}
          error={touched ? literalError : null}
          onChange={(event) => setLiteral(event.currentTarget.value)}
        />
        {valid && (
          <Text size="xs" c="dimmed">
            {t("placeholders.customPreview", {
              name: trimmedName,
              literal: trimmedLiteral.toUpperCase(),
            })}
          </Text>
        )}
        <Group justify="flex-end" gap="xs">
          <Button variant="default" onClick={close}>
            {tCommon("actions.cancel")}
          </Button>
          <Button onClick={submit}>{tCommon("actions.confirm")}</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
