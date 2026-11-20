import { Tooltip } from "@mantine/core";
import { IconGenderFemale, IconGenderMale, IconGenderTransgender } from "@tabler/icons-react";
import type { ParseKeys } from "i18next";
import { useTranslation } from "react-i18next";
import type { ActorGender } from "@/client/types.gen";

const GENDER_LABEL_KEY = {
  female: "browse.person.gender_female",
  transgender_female: "browse.person.gender_transgender_female",
  male: "browse.person.gender_male",
  unknown: "browse.person.gender_unknown",
} as const satisfies Record<ActorGender, ParseKeys<"metadata">>;

/** 已知性别显示对应符号; unknown 不画. */
export function GenderMark({
  gender,
  size = 14,
}: {
  gender: ActorGender | null | undefined;
  size?: number;
}) {
  const { t } = useTranslation("metadata");
  if (gender !== "female" && gender !== "male" && gender !== "transgender_female") return null;

  const Icon =
    gender === "female"
      ? IconGenderFemale
      : gender === "male"
        ? IconGenderMale
        : IconGenderTransgender;

  const color =
    gender === "female"
      ? "var(--mantine-color-pink-5)"
      : gender === "male"
        ? "var(--mantine-color-blue-5)"
        : "var(--mantine-color-grape-6)";

  const label = t(GENDER_LABEL_KEY[gender]);
  return (
    <Tooltip label={label}>
      <span
        style={{ display: "inline-flex", lineHeight: 0, verticalAlign: "middle" }}
        aria-label={label}
      >
        <Icon size={size} color={color} aria-hidden />
      </span>
    </Tooltip>
  );
}
