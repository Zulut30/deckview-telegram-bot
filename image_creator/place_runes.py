from PIL import Image


def place_runes(image, response):
    rune_slots = response.get("runeSlots")
    if not rune_slots:
        return image
    width, height = image.size
    total_runes = sum(rune_slots.values())
    total_width = total_runes * 200

    start_x = (width - total_width) // 2
    y_pos = height - 210  # Прижимаем к нижнему краю новой адаптивной высоты

    x_offset = 0
    for rune_name in rune_slots:
        for _ in range(rune_slots[rune_name]):
            rune = Image.open(f"death_knight/{rune_name}.png").convert("RGBA")
            rune = rune.resize((200, 200))

            image.paste(rune, (start_x + x_offset, y_pos), mask=rune)
            x_offset += 200

    return image
