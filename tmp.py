        dialog = ScalingDialog(
            label=label,
            scaling_enabled=settings["enabled"],
            current_gain=settings["gain"],
            current_offset=settings["offset"],
            parent=self,
        )

        if dialog.exec() == qtw.QDialog.DialogCode.Accepted:
            enabled, gain, offset = dialog.get_values()

            # Save settings for next dialog open
            self.scaling_settings[label] = {
                "enabled": enabled,
                "gain": gain,
                "offset": offset,
            }

            if enabled:
                self.add_scaling2signal(label, gain, offset)
                logger.info(f"Scaling for '{label}' enabled: gain={gain}, offset={offset}")
            else:
                self.remove_scaling_from_signal(label)
                logger.info(f"Scaling for '{label}' disabled.")

            # Update button color: red if active, default otherwise
            self.update_scaling_button_style(label, enabled)
