import { Checkbox, Switch } from "@mantine/core";
import type { AnyFieldApi } from "@tanstack/react-form";
import type { BooleanJSONSchema, FieldProps } from "../schema";
import { useFieldDomId } from "./dict-entry-form";
import { FieldChrome } from "./field-chrome";

export function BoolField({
  name,
  label,
  description,
  form,
  variant,
  booleanDisplay = "switch",
}: FieldProps<BooleanJSONSchema> & {
  booleanDisplay?: "switch" | "checkbox";
}) {
  const id = useFieldDomId(name);

  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => {
        const checked = field.state.value === true;
        const handleChange = (value: boolean) => field.handleChange(value);

        if (booleanDisplay === "checkbox") {
          return (
            <Checkbox
              id={id}
              label={label}
              checked={checked}
              onChange={(e) => handleChange(e.currentTarget.checked)}
              aria-label={label}
            />
          );
        }

        return (
          <FieldChrome
            variant={variant}
            layout="horizontal"
            htmlFor={id}
            label={label}
            description={description}
          >
            <Switch
              id={id}
              checked={checked}
              onChange={(e) =>
                handleChange(e.target instanceof HTMLInputElement && e.target.checked)
              }
              aria-label={label}
            />
          </FieldChrome>
        );
      }}
    </form.Field>
  );
}
