import { Switch } from "@mantine/core";
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
  trailing,
}: FieldProps<BooleanJSONSchema>) {
  const id = useFieldDomId(name);
  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => {
        const checked = field.state.value === true;
        return (
          <FieldChrome
            variant={variant}
            layout="horizontal"
            htmlFor={id}
            label={label}
            description={description}
            trailing={
              trailing !== null && typeof trailing === "object" && "render" in trailing
                ? trailing.render(checked)
                : trailing
            }
          >
            <Switch
              id={id}
              checked={checked}
              onChange={(e) => field.handleChange(e.currentTarget.checked)}
              aria-label={label}
            />
          </FieldChrome>
        );
      }}
    </form.Field>
  );
}
