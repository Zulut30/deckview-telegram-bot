use image::codecs::jpeg::JpegEncoder;
use image::imageops::FilterType;
use image::{DynamicImage, ImageBuffer, Rgb, Rgba, RgbaImage};
use imageproc::drawing::{draw_filled_rect_mut, draw_text_mut};
use imageproc::rect::Rect;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
use rusttype::{point, Font, Scale};
use std::fs;
use std::io::Cursor;
use std::path::Path;

#[derive(Debug)]
struct CardInput {
    path: String,
    count: u32,
    mana: i32,
    is_side: bool,
    card_type: String,
}

fn get_i64(payload: &PyDict, key: &str, default: i64) -> PyResult<i64> {
    match payload.get_item(key) {
        Some(value) => value.extract::<i64>(),
        None => Ok(default),
    }
}

fn get_string(payload: &PyDict, key: &str, default: &str) -> PyResult<String> {
    match payload.get_item(key) {
        Some(value) => value.extract::<String>(),
        None => Ok(default.to_string()),
    }
}

fn get_optional_string(payload: &PyDict, key: &str) -> PyResult<Option<String>> {
    match payload.get_item(key) {
        Some(value) => {
            if value.is_none() {
                Ok(None)
            } else {
                let text = value.extract::<String>()?;
                if text.trim().is_empty() {
                    Ok(None)
                } else {
                    Ok(Some(text))
                }
            }
        }
        None => Ok(None),
    }
}

fn parse_cards(payload: &PyDict) -> PyResult<Vec<CardInput>> {
    let cards_any = payload
        .get_item("cards")
        .ok_or_else(|| PyValueError::new_err("cards required"))?;
    let cards = cards_any.downcast::<PyList>()?;
    let mut out = Vec::with_capacity(cards.len());
    for item in cards.iter() {
        let row = item.downcast::<PyDict>()?;
        out.push(CardInput {
            path: get_string(row, "path", "")?,
            count: get_i64(row, "count", 1)? as u32,
            mana: get_i64(row, "mana", 0)? as i32,
            is_side: match row.get_item("is_side") {
                Some(value) => value.extract::<bool>()?,
                None => false,
            },
            card_type: get_string(row, "card_type", "")?,
        });
    }
    Ok(out)
}

fn make_gradient(width: u32, height: u32) -> RgbaImage {
    let stops = [[0u8, 0, 0], [13, 21, 33], [7, 14, 24], [13, 21, 33], [0, 0, 0]];
    let mut image = RgbaImage::new(width, height);
    let denom = if width > 1 { width - 1 } else { 1 };
    for x in 0..width {
        let pos = (x as f32 / denom as f32) * ((stops.len() - 1) as f32);
        let idx = (pos.floor() as usize).min(stops.len() - 2);
        let t = pos - idx as f32;
        let c0 = stops[idx];
        let c1 = stops[idx + 1];
        let px = Rgba([
            (c0[0] as f32 + (c1[0] as f32 - c0[0] as f32) * t) as u8,
            (c0[1] as f32 + (c1[1] as f32 - c0[1] as f32) * t) as u8,
            (c0[2] as f32 + (c1[2] as f32 - c0[2] as f32) * t) as u8,
            255,
        ]);
        for y in 0..height {
            image.put_pixel(x, y, px);
        }
    }
    image
}

fn alpha_bbox_at_least(image: &RgbaImage, threshold: u8) -> Option<(u32, u32, u32, u32)> {
    let (w, h) = image.dimensions();
    let (mut min_x, mut min_y, mut max_x, mut max_y) = (w, h, 0, 0);
    let mut found = false;
    for y in 0..h {
        for x in 0..w {
            if image.get_pixel(x, y).0[3] >= threshold {
                found = true;
                min_x = min_x.min(x);
                min_y = min_y.min(y);
                max_x = max_x.max(x);
                max_y = max_y.max(y);
            }
        }
    }
    if found {
        Some((min_x, min_y, max_x, max_y))
    } else {
        None
    }
}

fn alpha_bbox(image: &RgbaImage) -> Option<(u32, u32, u32, u32)> {
    alpha_bbox_at_least(image, 1)
}

fn visible_card_bbox(image: &RgbaImage) -> Option<(u32, u32, u32, u32)> {
    let (width, height) = image.dimensions();
    if width == 0 || height == 0 {
        return None;
    }
    let mut row_counts = vec![0u32; height as usize];
    let mut column_counts = vec![0u32; width as usize];
    for y in 0..height {
        for x in 0..width {
            if image.get_pixel(x, y).0[3] >= 128 {
                row_counts[y as usize] += 1;
                column_counts[x as usize] += 1;
            }
        }
    }
    let min_row_pixels = 2u32.max((width as f32 * 0.01).round() as u32);
    let min_column_pixels = 2u32.max((height as f32 * 0.01).round() as u32);
    let y0 = row_counts.iter().position(|&count| count >= min_row_pixels)? as u32;
    let y1 = row_counts.iter().rposition(|&count| count >= min_row_pixels)? as u32;
    let x0 = column_counts
        .iter()
        .position(|&count| count >= min_column_pixels)? as u32;
    let x1 = column_counts
        .iter()
        .rposition(|&count| count >= min_column_pixels)? as u32;
    Some((x0, y0, x1, y1))
}

fn overlay(dest: &mut RgbaImage, src: &RgbaImage, x0: i64, y0: i64) {
    let (dw, dh) = dest.dimensions();
    let (sw, sh) = src.dimensions();
    for sy in 0..sh {
        let dy = y0 + sy as i64;
        if dy < 0 || dy >= dh as i64 {
            continue;
        }
        for sx in 0..sw {
            let dx = x0 + sx as i64;
            if dx < 0 || dx >= dw as i64 {
                continue;
            }
            let sp = src.get_pixel(sx, sy).0;
            let alpha = sp[3] as f32 / 255.0;
            if alpha <= 0.0 {
                continue;
            }
            let dp = dest.get_pixel(dx as u32, dy as u32).0;
            let inv = 1.0 - alpha;
            dest.put_pixel(
                dx as u32,
                dy as u32,
                Rgba([
                    (sp[0] as f32 * alpha + dp[0] as f32 * inv) as u8,
                    (sp[1] as f32 * alpha + dp[1] as f32 * inv) as u8,
                    (sp[2] as f32 * alpha + dp[2] as f32 * inv) as u8,
                    255,
                ]),
            );
        }
    }
}

fn trim_transparent(image: RgbaImage) -> RgbaImage {
    if let Some((x0, y0, x1, y1)) = alpha_bbox(&image) {
        DynamicImage::ImageRgba8(image)
            .crop_imm(x0, y0, x1 - x0 + 1, y1 - y0 + 1)
            .to_rgba8()
    } else {
        image
    }
}

fn trim_visible_card(image: RgbaImage) -> RgbaImage {
    let bounds = visible_card_bbox(&image).or_else(|| alpha_bbox(&image));
    if let Some((x0, y0, x1, y1)) = bounds {
        DynamicImage::ImageRgba8(image)
            .crop_imm(x0, y0, x1 - x0 + 1, y1 - y0 + 1)
            .to_rgba8()
    } else {
        image
    }
}

fn placeholder(width: u32, height: u32) -> RgbaImage {
    ImageBuffer::from_pixel(width, height, Rgba([45, 55, 70, 255]))
}

fn load_rgba(path: &str) -> Option<RgbaImage> {
    image::open(Path::new(path)).ok().map(|im| im.to_rgba8())
}

fn fit_image(image: &RgbaImage, cell_w: u32, cell_h: u32, card_type: &str) -> (RgbaImage, i64, i64, u32, u32) {
    let (iw, ih) = image.dimensions();
    if iw == 0 || ih == 0 {
        return (RgbaImage::new(cell_w, cell_h), 0, 0, 0, 0);
    }
    // Match the Pillow renderer: preserve the original card proportions and
    // use the wider grid cell for naturally broad frames.
    let scale = (cell_h as f32 / ih as f32).min(cell_w as f32 / iw as f32);
    let mut nw = ((iw as f32 * scale).round() as u32).clamp(1, cell_w);
    let nh = ((ih as f32 * scale).round() as u32).clamp(1, cell_h);
    if card_type.trim().eq_ignore_ascii_case("LOCATION") && nh == cell_h {
        let standard_width = ((cell_h as f32 * (477.0 / 677.0)).round() as u32)
            .clamp(1, cell_w);
        nw = nw.max(standard_width);
    }
    let resized = DynamicImage::ImageRgba8(image.clone())
        .resize_exact(nw, nh, FilterType::Lanczos3)
        .to_rgba8();
    let mut cell = RgbaImage::new(cell_w, cell_h);
    let ox = (cell_w as i64 - nw as i64) / 2;
    // Card renders do not all have the same transparent crop/aspect ratio.
    // A common top baseline keeps spells, locations and minions level.
    let oy = 0i64;
    overlay(&mut cell, &resized, ox, oy);
    (cell, ox, oy, nw, nh)
}

fn tint_side(image: &mut RgbaImage) {
    for pixel in image.pixels_mut() {
        let mut p = pixel.0;
        p[0] = p[0].saturating_add(100);
        p[1] = p[1].saturating_add(40);
        p[2] = p[2].saturating_add(45);
        *pixel = Rgba(p);
    }
}

fn load_font(path: &str) -> Option<Font<'static>> {
    Font::try_from_vec(fs::read(path).ok()?)
}

fn text_width(font: &Font, scale: Scale, text: &str) -> i32 {
    let glyphs: Vec<_> = font.layout(text, scale, point(0.0, 0.0)).collect();
    glyphs
        .last()
        .and_then(|glyph| glyph.pixel_bounding_box())
        .map(|bb| bb.max.x)
        .unwrap_or(0)
}

fn draw_centered_text(
    image: &mut RgbaImage,
    font: &Font,
    text: &str,
    center_x: i32,
    center_y: i32,
    max_width: i32,
    start_size: f32,
) {
    let mut size = start_size;
    while size > 28.0 {
        if text_width(font, Scale::uniform(size), text) <= max_width {
            break;
        }
        size -= 4.0;
    }
    let scale = Scale::uniform(size);
    let x = center_x - text_width(font, scale, text) / 2;
    let y = center_y - (size as i32 / 2);
    for dx in [-3, -2, -1, 1, 2, 3] {
        for dy in [-3, -2, -1, 1, 2, 3] {
            draw_text_mut(image, Rgba([0, 0, 0, 255]), x + dx, y + dy, scale, font, text);
        }
    }
    draw_text_mut(image, Rgba([255, 255, 255, 255]), x, y, scale, font, text);
}

fn draw_title(image: &mut RgbaImage, title: &str, font: Option<&Font>, width: u32) {
    if title.trim().is_empty() {
        return;
    }
    if let Some(font) = font {
        draw_centered_text(
            image,
            font,
            title,
            (width / 2) as i32,
            125,
            width as i32 - 120,
            112.0,
        );
    }
}

fn draw_dust(image: &mut RgbaImage, dust_text: &str, dust_asset_path: &str, font: Option<&Font>, cards_bottom_y: u32) {
    let font = match font {
        Some(font) => font,
        None => return,
    };
    let scale = Scale::uniform(148.0);
    let text_w = text_width(font, scale, dust_text).max(1);
    let text_h = 112i32;
    let mut dust = load_rgba(dust_asset_path).unwrap_or_else(|| RgbaImage::new(1, 1));
    let dust_w = ((text_h as f32) * 1.1) as u32;
    let dust_h = ((dust_w as f32) * dust.height() as f32 / dust.width().max(1) as f32).round() as u32;
    dust = DynamicImage::ImageRgba8(dust)
        .resize_exact(dust_w.max(1), dust_h.max(1), FilterType::Lanczos3)
        .to_rgba8();
    let footer_h = image.height().saturating_sub(cards_bottom_y).max(1);
    let center_y = cards_bottom_y as i32 + ((footer_h as f32) * 0.6) as i32;
    let spacing = 15i32;
    let total_w = text_w + spacing + dust_w as i32;
    let start_x = (image.width() as i32 - total_w) / 2;
    let text_y = center_y - text_h / 2;
    overlay(image, &dust, (start_x + text_w + spacing) as i64, (center_y - dust_h as i32 / 2) as i64);
    for dx in [-3, -2, -1, 1, 2, 3] {
        for dy in [-3, -2, -1, 1, 2, 3] {
            draw_text_mut(image, Rgba([0, 0, 0, 255]), start_x + dx, text_y + dy, scale, font, dust_text);
        }
    }
    draw_text_mut(image, Rgba([255, 255, 255, 255]), start_x, text_y, scale, font, dust_text);
}

fn draw_mana_curve(image: &mut RgbaImage, cards: &[CardInput], cards_bottom_y: u32, font: Option<&Font>) {
    let footer_h = image.height().saturating_sub(cards_bottom_y).max(1);
    let dust_center_y = cards_bottom_y as i32 + ((footer_h as f32) * 0.6) as i32;
    let mut curve = [0u32; 8];
    for card in cards {
        let bucket = if card.mana >= 7 { 7 } else { card.mana.max(0) as usize };
        curve[bucket] += card.count;
    }
    let max_count = curve.iter().copied().max().unwrap_or(1).max(1);
    let chart_w = 976u32.min(((image.width() as f32) * 0.5835) as u32);
    let chart_h = 318u32;
    let chart_x = 50i32 + ((image.width() as f32) * 0.025) as i32;
    let chart_y = dust_center_y - (chart_h as i32 / 2);
    draw_filled_rect_mut(
        image,
        Rect::at(chart_x - 16, chart_y - 16).of_size(chart_w + 32, chart_h + 44),
        Rgba([6, 10, 16, 220]),
    );
    let gap = 10u32;
    let bar_w = ((chart_w - gap * 7) / 8).max(10);
    let base_y = chart_y + chart_h as i32;
    let colors = [
        [102, 178, 216],
        [102, 170, 216],
        [102, 157, 216],
        [102, 144, 216],
        [102, 131, 216],
        [102, 119, 216],
        [102, 106, 216],
        [102, 93, 216],
    ];
    for i in 0..8usize {
        let bar_h = ((curve[i] as f32 / max_count as f32) * (chart_h as f32 - 20.0)) as i32;
        let x0 = chart_x + i as i32 * (bar_w as i32 + gap as i32);
        let y0 = base_y - bar_h;
        let color = colors[i];
        draw_filled_rect_mut(
            image,
            Rect::at(x0, y0).of_size(bar_w, bar_h.max(0) as u32),
            Rgba([color[0], color[1], color[2], 255]),
        );
        if let Some(font) = font {
            let label = if i == 7 { "7+".to_string() } else { i.to_string() };
            let scale = Scale::uniform(36.0);
            let tx = x0 + bar_w as i32 / 2 - text_width(font, scale, &label) / 2;
            draw_text_mut(image, Rgba([235, 245, 255, 255]), tx, base_y + 20, scale, font, &label);
        }
    }
}

fn draw_class_art(image: &mut RgbaImage, class_asset_path: &str) {
    if let Some(class_img) = load_rgba(class_asset_path) {
        let c_h = 900u32;
        let c_w = ((c_h as f32) * class_img.width() as f32 / class_img.height().max(1) as f32) as u32;
        let mut class_img = DynamicImage::ImageRgba8(class_img)
            .resize_exact(c_w.max(1), c_h, FilterType::Lanczos3)
            .to_rgba8();
        for pixel in class_img.pixels_mut() {
            let mut p = pixel.0;
            p[0] = ((p[0] as f32) * 0.88) as u8;
            p[1] = ((p[1] as f32) * 0.88) as u8;
            p[2] = ((p[2] as f32) * 0.88) as u8;
            p[3] = ((p[3] as f32) * 0.85) as u8;
            *pixel = Rgba(p);
        }
        overlay(
            image,
            &class_img,
            image.width() as i64 - class_img.width() as i64,
            image.height() as i64 - class_img.height() as i64,
        );
    }
}

fn encode_jpeg(image: &RgbaImage, quality: u8) -> PyResult<Vec<u8>> {
    let mut rgb = ImageBuffer::<Rgb<u8>, Vec<u8>>::new(image.width(), image.height());
    for (x, y, pixel) in rgb.enumerate_pixels_mut() {
        let p = image.get_pixel(x, y).0;
        *pixel = Rgb([p[0], p[1], p[2]]);
    }
    let mut out = Vec::new();
    let mut cursor = Cursor::new(&mut out);
    JpegEncoder::new_with_quality(&mut cursor, quality)
        .encode_image(&DynamicImage::ImageRgb8(rgb))
        .map_err(|e| PyValueError::new_err(format!("jpeg encode failed: {e}")))?;
    Ok(out)
}

#[pyfunction]
fn render_deck_image(py: Python<'_>, payload: &PyDict) -> PyResult<PyObject> {
    let cards = parse_cards(payload)?;
    if cards.is_empty() {
        return Err(PyValueError::new_err("cards empty"));
    }
    let cell_w = get_i64(payload, "cell_w", 375)? as u32;
    let cell_h = get_i64(payload, "cell_h", 507)? as u32;
    let row_gap = get_i64(payload, "row_gap", 40)? as u32;
    let top_margin = get_i64(payload, "top_margin", 0)? as u32;
    let bottom_margin = get_i64(payload, "bottom_margin", 800)? as u32;
    let max_output_side = get_i64(payload, "max_output_side", 1920)? as u32;
    let deck_cost = get_i64(payload, "deck_cost", 0)?;
    let water_path = get_string(payload, "water_path", "x2.png")?;
    let dust_asset_path = get_string(payload, "dust_asset_path", "assets/dust.png")?;
    let class_asset_path = get_string(payload, "class_asset_path", "")?;
    let font_path = get_string(payload, "font_path", "")?;
    let deck_name = get_optional_string(payload, "deck_name")?;

    let n_cards = cards.len() as u32;
    let requested_cols = get_i64(payload, "n_cols", 0)?.max(0) as u32;
    let automatic_cols = (3000 / cell_w).max(1);
    let n_cols = n_cards
        .min(if requested_cols > 0 { requested_cols } else { automatic_cols })
        .max(1);
    let n_rows = (n_cards + n_cols - 1) / n_cols;
    let width = n_cols * cell_w;
    let height = n_rows * (cell_h + row_gap) + bottom_margin + top_margin;
    let mut canvas = make_gradient(width, height);
    let font = load_font(&font_path);

    if let Some(title) = deck_name.as_ref() {
        draw_title(&mut canvas, title, font.as_ref(), width);
    }

    let water_raw = load_rgba(&water_path).unwrap_or_else(|| RgbaImage::new(1, 1));
    let water_resized = if n_cards <= 18 {
        DynamicImage::ImageRgba8(water_raw).resize_exact(214, 121, FilterType::Lanczos3).to_rgba8()
    } else if n_cards <= 32 {
        DynamicImage::ImageRgba8(water_raw).resize_exact(141, 80, FilterType::Lanczos3).to_rgba8()
    } else {
        DynamicImage::ImageRgba8(water_raw).resize_exact(124, 70, FilterType::Lanczos3).to_rgba8()
    };
    let water = trim_transparent(water_resized);

    let mut col = 0u32;
    let mut row = top_margin;
    for card in &cards {
        let raw = load_rgba(&card.path).unwrap_or_else(|| placeholder(cell_w, cell_h));
        let mut cropped = trim_visible_card(raw);
        if card.is_side {
            tint_side(&mut cropped);
        }
        let (cell, ox, oy, nw, nh) = fit_image(&cropped, cell_w, cell_h, &card.card_type);
        overlay(&mut canvas, &cell, col as i64, row as i64);
        if card.count == 2 && water.width() > 1 && water.height() > 1 {
            let drop = 12i64.max(28i64.min((nh / 20) as i64));
            let mut wx = col as i64 + ox + ((nw as i64 - water.width() as i64).max(0) / 2);
            let mut wy = row as i64 + oy + nh as i64 + drop;
            let next_row_y = row as i64 + cell_h as i64 + row_gap as i64;
            let max_wy = next_row_y - water.height() as i64 - 2;
            if wy > max_wy {
                wy = max_wy;
            }
            wy = wy.max(row as i64 + oy + nh as i64);
            wx = wx.max(0).min(canvas.width() as i64 - water.width() as i64);
            wy = wy.max(0).min(canvas.height() as i64 - water.height() as i64);
            overlay(&mut canvas, &water, wx, wy);
        }
        col += cell_w;
        if col >= width {
            col = 0;
            row += cell_h + row_gap;
        }
    }

    let cards_bottom_y = n_rows * (cell_h + row_gap) + top_margin;
    draw_dust(&mut canvas, &deck_cost.to_string(), &dust_asset_path, font.as_ref(), cards_bottom_y);
    draw_mana_curve(&mut canvas, &cards, cards_bottom_y, font.as_ref());
    if !class_asset_path.trim().is_empty() {
        draw_class_art(&mut canvas, &class_asset_path);
    }

    if canvas.width() > max_output_side || canvas.height() > max_output_side {
        let scale = max_output_side as f32 / canvas.width().max(canvas.height()) as f32;
        let new_w = ((canvas.width() as f32) * scale).max(1.0) as u32;
        let new_h = ((canvas.height() as f32) * scale).max(1.0) as u32;
        canvas = DynamicImage::ImageRgba8(canvas)
            .resize_exact(new_w, new_h, FilterType::Lanczos3)
            .to_rgba8();
    }

    let bytes = encode_jpeg(&canvas, 92)?;
    Ok(PyBytes::new(py, &bytes).into())
}

#[pymodule]
fn deckview_core(_py: Python<'_>, module: &PyModule) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(render_deck_image, module)?)?;
    Ok(())
}
