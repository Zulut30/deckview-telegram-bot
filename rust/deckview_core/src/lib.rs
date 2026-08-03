use ab_glyph::{Font, FontArc, PxScale, ScaleFont};
use fast_image_resize::{FilterType as FastFilterType, ResizeAlg, ResizeOptions, Resizer};
use image::codecs::jpeg::JpegEncoder;
use image::{DynamicImage, ImageBuffer, Rgb, Rgba, RgbaImage};
use imageproc::drawing::{draw_filled_rect_mut, draw_line_segment_mut, draw_text_mut};
use imageproc::rect::Rect;
use pyo3::create_exception;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBytes, PyDict, PyList};
use rayon::prelude::*;
use rayon::{ThreadPool, ThreadPoolBuilder};
use std::collections::HashMap;
use std::env;
use std::fs;
use std::io::Cursor;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::UNIX_EPOCH;

const SCHEMA_VERSION: i64 = 2;
const RENDERER_VERSION: &str = "deckview-native/0.3.0";
const MAX_CARD_COUNT: usize = 100;
const MAX_CANVAS_SIDE: u32 = 16_384;
const MAX_CANVAS_PIXELS: u64 = 100_000_000;

create_exception!(deckview_core, RenderContractError, PyValueError);
create_exception!(
    deckview_core,
    NativeRenderError,
    pyo3::exceptions::PyRuntimeError
);

#[derive(Clone, Debug)]
struct CardInput {
    path: String,
    count: u32,
    mana: i32,
    is_side: bool,
    card_type: String,
}

#[derive(Clone, Copy, Debug, Hash, PartialEq, Eq)]
enum ImageStyle {
    Classic,
    Parchment,
    Custom,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ManaCurveMode {
    Chart,
    Hidden,
    Image,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum DustMode {
    Normal,
    Large,
    Hidden,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ClassArtMode {
    Class,
    Logo,
}

#[derive(Clone, Debug)]
struct BackgroundInput {
    style: ImageStyle,
    kind: String,
    value: String,
    path: String,
    blur: u32,
}

#[derive(Clone, Debug)]
struct RuneInput {
    path: String,
    count: u32,
}

#[derive(Debug)]
struct RenderInput {
    renderer_version: String,
    cards: Vec<CardInput>,
    cell_w: u32,
    cell_h: u32,
    row_gap: u32,
    top_margin: u32,
    bottom_margin: u32,
    max_output_side: u32,
    jpeg_quality: u8,
    deck_cost: i64,
    n_cols: u32,
    dust_asset_path: String,
    class_asset_path: String,
    font_path: String,
    ornament_font_path: String,
    parchment_path: String,
    wood_frame_path: String,
    background: BackgroundInput,
    title_scale: f32,
    dust_mode: DustMode,
    class_art_mode: ClassArtMode,
    mana_curve_mode: ManaCurveMode,
    mana_curve_path: String,
    runes: Vec<RuneInput>,
    deck_name: Option<String>,
}

#[derive(Clone, Debug, Hash, PartialEq, Eq)]
struct CardCacheKey {
    renderer_version: String,
    path: String,
    source_len: u64,
    source_mtime_ns: u128,
    cell_w: u32,
    cell_h: u32,
    is_side: bool,
    card_type: String,
}

#[derive(Clone, Debug, Hash, PartialEq, Eq)]
struct BackgroundCacheKey {
    renderer_version: String,
    width: u32,
    height: u32,
    style: ImageStyle,
    kind: String,
    value: String,
    path: String,
    source_len: u64,
    source_mtime_ns: u128,
    parchment_len: u64,
    parchment_mtime_ns: u128,
    blur: u32,
}

#[derive(Debug)]
struct PreparedCard {
    cell: RgbaImage,
    offset_x: i64,
    offset_y: i64,
    visible_w: u32,
    visible_h: u32,
}

static CARD_CACHE: OnceLock<Mutex<HashMap<CardCacheKey, Arc<PreparedCard>>>> = OnceLock::new();
static BACKGROUND_CACHE: OnceLock<Mutex<HashMap<BackgroundCacheKey, Arc<RgbaImage>>>> =
    OnceLock::new();
static RENDER_POOL: OnceLock<Result<ThreadPool, String>> = OnceLock::new();
static BACKGROUND_CACHE_HITS: AtomicUsize = AtomicUsize::new(0);
static BACKGROUND_CACHE_MISSES: AtomicUsize = AtomicUsize::new(0);
static BACKGROUND_CACHE_EVICTIONS: AtomicUsize = AtomicUsize::new(0);

fn card_cache() -> &'static Mutex<HashMap<CardCacheKey, Arc<PreparedCard>>> {
    CARD_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn background_cache() -> &'static Mutex<HashMap<BackgroundCacheKey, Arc<RgbaImage>>> {
    BACKGROUND_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn configured_threads() -> usize {
    let available = std::thread::available_parallelism()
        .map(usize::from)
        .unwrap_or(1);
    env::var("DECKVIEW_RUST_THREADS")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or_else(|| available.min(4))
        .clamp(1, 16)
}

fn render_pool() -> Result<&'static ThreadPool, String> {
    RENDER_POOL
        .get_or_init(|| {
            ThreadPoolBuilder::new()
                .num_threads(configured_threads())
                .thread_name(|index| format!("deckview-render-{index}"))
                .build()
                .map_err(|error| format!("native render pool initialization failed: {error}"))
        })
        .as_ref()
        .map_err(Clone::clone)
}

fn card_cache_limit() -> usize {
    env::var("DECKVIEW_RUST_CARD_CACHE_MAX")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(256)
        .clamp(32, 4096)
}

fn background_cache_limit() -> usize {
    env::var("DECKVIEW_RUST_BACKGROUND_CACHE_MAX")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(8)
        .clamp(1, 64)
}

type ContractResult<T> = Result<T, String>;

fn required_item<'py>(
    payload: &Bound<'py, PyDict>,
    key: &str,
) -> ContractResult<Bound<'py, PyAny>> {
    payload
        .get_item(key)
        .map_err(|error| format!("{key}: {error}"))?
        .ok_or_else(|| format!("{key} is required"))
}

fn get_i64(payload: &Bound<'_, PyDict>, key: &str, default: i64) -> ContractResult<i64> {
    match payload
        .get_item(key)
        .map_err(|error| format!("{key}: {error}"))?
    {
        Some(value) => value
            .extract::<i64>()
            .map_err(|error| format!("{key} must be an integer: {error}")),
        None => Ok(default),
    }
}

fn bounded_i64(
    payload: &Bound<'_, PyDict>,
    key: &str,
    default: i64,
    minimum: i64,
    maximum: i64,
) -> ContractResult<i64> {
    let value = get_i64(payload, key, default)?;
    if !(minimum..=maximum).contains(&value) {
        return Err(format!(
            "{key} must be between {minimum} and {maximum}, got {value}"
        ));
    }
    Ok(value)
}

fn bounded_f64(
    payload: &Bound<'_, PyDict>,
    key: &str,
    default: f64,
    minimum: f64,
    maximum: f64,
) -> ContractResult<f64> {
    let value = match payload
        .get_item(key)
        .map_err(|error| format!("{key}: {error}"))?
    {
        Some(value) => value
            .extract::<f64>()
            .map_err(|error| format!("{key} must be numeric: {error}"))?,
        None => default,
    };
    if !value.is_finite() || value < minimum || value > maximum {
        return Err(format!(
            "{key} must be between {minimum} and {maximum}, got {value}"
        ));
    }
    Ok(value)
}

fn get_string(
    payload: &Bound<'_, PyDict>,
    key: &str,
    default: &str,
    maximum_length: usize,
) -> ContractResult<String> {
    let value = match payload
        .get_item(key)
        .map_err(|error| format!("{key}: {error}"))?
    {
        Some(value) => value
            .extract::<String>()
            .map_err(|error| format!("{key} must be a string: {error}"))?,
        None => default.to_string(),
    };
    if value.len() > maximum_length {
        return Err(format!("{key} exceeds {maximum_length} bytes"));
    }
    Ok(value)
}

fn get_optional_string(
    payload: &Bound<'_, PyDict>,
    key: &str,
    maximum_length: usize,
) -> ContractResult<Option<String>> {
    match payload
        .get_item(key)
        .map_err(|error| format!("{key}: {error}"))?
    {
        Some(value) => {
            if value.is_none() {
                Ok(None)
            } else {
                let text = value
                    .extract::<String>()
                    .map_err(|error| format!("{key} must be a string: {error}"))?;
                if text.len() > maximum_length {
                    return Err(format!("{key} exceeds {maximum_length} bytes"));
                }
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

fn parse_image_style(value: &str) -> ContractResult<ImageStyle> {
    match value {
        "classic" => Ok(ImageStyle::Classic),
        "parchment" => Ok(ImageStyle::Parchment),
        "custom" => Ok(ImageStyle::Custom),
        _ => Err(format!("background.style has unsupported value {value:?}")),
    }
}

fn parse_mana_curve_mode(value: &str) -> ContractResult<ManaCurveMode> {
    match value {
        "chart" => Ok(ManaCurveMode::Chart),
        "hidden" => Ok(ManaCurveMode::Hidden),
        "image" => Ok(ManaCurveMode::Image),
        _ => Err(format!("mana_curve.mode has unsupported value {value:?}")),
    }
}

fn parse_dust_mode(value: &str) -> ContractResult<DustMode> {
    match value {
        "normal" => Ok(DustMode::Normal),
        "large" => Ok(DustMode::Large),
        "hidden" => Ok(DustMode::Hidden),
        _ => Err(format!("dust.mode has unsupported value {value:?}")),
    }
}

fn parse_class_art_mode(value: &str) -> ContractResult<ClassArtMode> {
    match value {
        "class" => Ok(ClassArtMode::Class),
        "logo" => Ok(ClassArtMode::Logo),
        _ => Err(format!("class_art.mode has unsupported value {value:?}")),
    }
}

fn parse_string_list(payload: &Bound<'_, PyDict>, key: &str) -> ContractResult<Vec<String>> {
    let values_any = required_item(payload, key)?;
    let values = values_any
        .cast::<PyList>()
        .map_err(|error| format!("{key} must be a list: {error}"))?;
    if values.is_empty() || values.len() > 128 {
        return Err(format!("{key} must contain between 1 and 128 paths"));
    }
    values
        .iter()
        .map(|value| {
            let path = value
                .extract::<String>()
                .map_err(|error| format!("{key} entries must be strings: {error}"))?;
            if path.is_empty() || path.len() > 4096 {
                return Err(format!("{key} contains an invalid path length"));
            }
            Ok(path)
        })
        .collect()
}

fn parse_cards(payload: &Bound<'_, PyDict>) -> ContractResult<Vec<CardInput>> {
    let cards_any = required_item(payload, "cards")?;
    let cards = cards_any
        .cast::<PyList>()
        .map_err(|error| format!("cards must be a list: {error}"))?;
    if cards.is_empty() || cards.len() > MAX_CARD_COUNT {
        return Err(format!(
            "cards must contain between 1 and {MAX_CARD_COUNT} entries"
        ));
    }
    let mut out = Vec::with_capacity(cards.len());
    for (index, item) in cards.iter().enumerate() {
        let row = item
            .cast::<PyDict>()
            .map_err(|error| format!("cards[{index}] must be a dictionary: {error}"))?;
        let path = get_string(row, "path", "", 4096)?;
        if path.is_empty() {
            return Err(format!("cards[{index}].path is required"));
        }
        out.push(CardInput {
            path,
            count: bounded_i64(row, "count", 1, 1, 10)? as u32,
            // Sideboard cards use a small negative sentinel in the Python
            // resolver so they sort after the main deck. The mana curve
            // intentionally clamps that sentinel into the zero bucket.
            mana: bounded_i64(row, "mana", 0, -20, 100)? as i32,
            is_side: match row
                .get_item("is_side")
                .map_err(|error| format!("cards[{index}].is_side: {error}"))?
            {
                Some(value) => value.extract::<bool>().map_err(|error| {
                    format!("cards[{index}].is_side must be a boolean: {error}")
                })?,
                None => false,
            },
            card_type: get_string(row, "card_type", "", 64)?,
        });
    }
    Ok(out)
}

fn parse_runes(payload: &Bound<'_, PyDict>) -> ContractResult<Vec<RuneInput>> {
    let Some(runes_any) = payload
        .get_item("runes")
        .map_err(|error| format!("runes: {error}"))?
    else {
        return Ok(Vec::new());
    };
    let runes = runes_any
        .cast::<PyList>()
        .map_err(|error| format!("runes must be a list: {error}"))?;
    if runes.len() > 3 {
        return Err("runes must contain at most three entries".to_string());
    }
    let mut out = Vec::with_capacity(runes.len());
    let mut total = 0u32;
    for (index, item) in runes.iter().enumerate() {
        let row = item
            .cast::<PyDict>()
            .map_err(|error| format!("runes[{index}] must be a dictionary: {error}"))?;
        let path = get_string(row, "path", "", 4096)?;
        let count = bounded_i64(row, "count", 0, 0, 3)? as u32;
        total = total.saturating_add(count);
        if total > 3 {
            return Err("runes total count must not exceed three".to_string());
        }
        if count > 0 && path.is_empty() {
            return Err(format!("runes[{index}].path is required"));
        }
        if count > 0 {
            out.push(RuneInput { path, count });
        }
    }
    Ok(out)
}

fn make_gradient(width: u32, height: u32) -> Result<RgbaImage, String> {
    let stops = [
        [0u8, 0, 0],
        [13, 21, 33],
        [7, 14, 24],
        [13, 21, 33],
        [0, 0, 0],
    ];
    let denom = if width > 1 { width - 1 } else { 1 };
    let mut row = Vec::with_capacity(width as usize * 4);
    for x in 0..width {
        let pos = (x as f32 / denom as f32) * ((stops.len() - 1) as f32);
        let idx = (pos.floor() as usize).min(stops.len() - 2);
        let t = pos - idx as f32;
        let c0 = stops[idx];
        let c1 = stops[idx + 1];
        row.extend_from_slice(&[
            (c0[0] as f32 + (c1[0] as f32 - c0[0] as f32) * t) as u8,
            (c0[1] as f32 + (c1[1] as f32 - c0[1] as f32) * t) as u8,
            (c0[2] as f32 + (c1[2] as f32 - c0[2] as f32) * t) as u8,
            255,
        ]);
    }
    let mut pixels = vec![0u8; row.len() * height as usize];
    pixels
        .chunks_mut(row.len())
        .for_each(|destination| destination.copy_from_slice(&row));
    RgbaImage::from_raw(width, height, pixels)
        .ok_or_else(|| "gradient buffer dimensions do not match".to_string())
}

fn parse_hex_rgb(value: &str) -> Result<[u8; 3], String> {
    let value = value.trim();
    if value.len() != 7 || !value.starts_with('#') {
        return Err(format!("invalid gradient color {value:?}"));
    }
    let component = |start: usize| {
        u8::from_str_radix(&value[start..start + 2], 16)
            .map_err(|_| format!("invalid gradient color {value:?}"))
    };
    Ok([component(1)?, component(3)?, component(5)?])
}

fn make_custom_gradient(width: u32, height: u32, value: &str) -> Result<RgbaImage, String> {
    let colors = value.split(',').map(str::trim).collect::<Vec<_>>();
    if colors.len() != 2 {
        return Err("background.value must contain two #RRGGBB colors".to_string());
    }
    let start = parse_hex_rgb(colors[0])?;
    let end = parse_hex_rgb(colors[1])?;
    let denominator = height.saturating_sub(1).max(1) as f32;
    let mut canvas = RgbaImage::new(width, height);
    for y in 0..height {
        let ratio = y as f32 / denominator;
        let color = [
            (start[0] as f32 + (end[0] as f32 - start[0] as f32) * ratio) as u8,
            (start[1] as f32 + (end[1] as f32 - start[1] as f32) * ratio) as u8,
            (start[2] as f32 + (end[2] as f32 - start[2] as f32) * ratio) as u8,
            255,
        ];
        for x in 0..width {
            canvas.put_pixel(x, y, Rgba(color));
        }
    }
    Ok(canvas)
}

fn cover_image(image: &RgbaImage, width: u32, height: u32) -> Result<RgbaImage, String> {
    let (source_w, source_h) = image.dimensions();
    if source_w == 0 || source_h == 0 {
        return Err("background image is empty".to_string());
    }
    // Crop to the destination aspect ratio before resizing. Resizing a very
    // wide banner to a tall deck first can otherwise allocate tens of
    // thousands of unnecessary pixels only to discard them immediately.
    let (crop_x, crop_y, crop_w, crop_h) =
        if u64::from(source_w) * u64::from(height) > u64::from(source_h) * u64::from(width) {
            let crop_w = ((u64::from(source_h) * u64::from(width)) / u64::from(height))
                .max(1)
                .min(u64::from(source_w)) as u32;
            ((source_w - crop_w) / 2, 0, crop_w, source_h)
        } else {
            let crop_h = ((u64::from(source_w) * u64::from(height)) / u64::from(width))
                .max(1)
                .min(u64::from(source_h)) as u32;
            (0, (source_h - crop_h) / 2, source_w, crop_h)
        };
    let cropped = DynamicImage::ImageRgba8(image.clone())
        .crop_imm(crop_x, crop_y, crop_w, crop_h)
        .to_rgba8();
    resize_rgba(&cropped, width, height)
}

fn parchment_canvas(width: u32, height: u32, path: &str) -> Result<RgbaImage, String> {
    let texture = load_rgba(path)
        .ok_or_else(|| format!("parchment image is missing or unreadable: {path}"))?;
    let (texture_w, texture_h) = texture.dimensions();
    if texture_w == 0 || texture_h == 0 {
        return Err("parchment image is empty".to_string());
    }
    let mut canvas = RgbaImage::new(width, height);
    let mut y = 0u32;
    while y < height {
        let mut x = 0u32;
        while x < width {
            overlay(&mut canvas, &texture, x as i64, y as i64);
            x = x.saturating_add(texture_w);
        }
        y = y.saturating_add(texture_h);
    }
    for pixel in canvas.pixels_mut() {
        let source = pixel.0;
        let wash_alpha = 34u16;
        let inverse = 255 - wash_alpha;
        *pixel = Rgba([
            ((247u16 * wash_alpha + source[0] as u16 * inverse + 127) / 255) as u8,
            ((232u16 * wash_alpha + source[1] as u16 * inverse + 127) / 255) as u8,
            ((191u16 * wash_alpha + source[2] as u16 * inverse + 127) / 255) as u8,
            255,
        ]);
    }
    Ok(canvas)
}

fn make_background(
    width: u32,
    height: u32,
    background: &BackgroundInput,
    parchment_path: &str,
) -> Result<RgbaImage, String> {
    match background.style {
        ImageStyle::Classic => make_gradient(width, height),
        ImageStyle::Parchment => parchment_canvas(width, height, parchment_path),
        ImageStyle::Custom if background.kind == "gradient" => {
            make_custom_gradient(width, height, &background.value)
        }
        ImageStyle::Custom if background.kind == "image" => {
            let source = load_rgba(&background.path).ok_or_else(|| {
                format!(
                    "custom background is missing or unreadable: {}",
                    background.path
                )
            })?;
            let fitted = cover_image(&source, width, height)?;
            if background.blur == 0 {
                Ok(fitted)
            } else {
                // Keep blur strength proportional to the final canvas while
                // doing the convolution at a bounded working resolution.
                let maximum_working_side = 1_024u32;
                let maximum_side = width.max(height);
                let working_scale = (maximum_working_side as f32 / maximum_side as f32).min(1.0);
                let working_width = ((width as f32) * working_scale).round().max(1.0) as u32;
                let working_height = ((height as f32) * working_scale).round().max(1.0) as u32;
                let working = if working_scale < 1.0 {
                    resize_rgba(&fitted, working_width, working_height)?
                } else {
                    fitted
                };
                let radius = working_width.max(working_height) as f32 / 70.0
                    * (background.blur as f32 / 100.0);
                let blurred = DynamicImage::ImageRgba8(working).blur(radius).to_rgba8();
                if blurred.dimensions() == (width, height) {
                    Ok(blurred)
                } else {
                    resize_rgba(&blurred, width, height)
                }
            }
        }
        ImageStyle::Custom => parchment_canvas(width, height, parchment_path),
    }
}

fn make_background_cached(
    width: u32,
    height: u32,
    background: &BackgroundInput,
    parchment_path: &str,
    renderer_version: &str,
) -> Result<RgbaImage, String> {
    let (source_len, source_mtime_ns) = if background.path.is_empty() {
        (0, 0)
    } else {
        source_revision(&background.path)
    };
    let (parchment_len, parchment_mtime_ns) = source_revision(parchment_path);
    let key = BackgroundCacheKey {
        renderer_version: renderer_version.to_string(),
        width,
        height,
        style: background.style,
        kind: background.kind.clone(),
        value: background.value.clone(),
        path: background.path.clone(),
        source_len,
        source_mtime_ns,
        parchment_len,
        parchment_mtime_ns,
        blur: background.blur,
    };
    {
        let cache = background_cache()
            .lock()
            .map_err(|_| "native background cache lock poisoned".to_string())?;
        if let Some(hit) = cache.get(&key) {
            BACKGROUND_CACHE_HITS.fetch_add(1, Ordering::Relaxed);
            return Ok(hit.as_ref().clone());
        }
    }
    BACKGROUND_CACHE_MISSES.fetch_add(1, Ordering::Relaxed);
    let rendered = make_background(width, height, background, parchment_path)?;
    let mut cache = background_cache()
        .lock()
        .map_err(|_| "native background cache lock poisoned".to_string())?;
    if let Some(hit) = cache.get(&key) {
        return Ok(hit.as_ref().clone());
    }
    if cache.len() >= background_cache_limit()
        && let Some(evicted) = cache.keys().next().cloned()
    {
        cache.remove(&evicted);
        BACKGROUND_CACHE_EVICTIONS.fetch_add(1, Ordering::Relaxed);
    }
    cache.insert(key, Arc::new(rendered.clone()));
    Ok(rendered)
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
    let y0 = row_counts
        .iter()
        .position(|&count| count >= min_row_pixels)? as u32;
    let y1 = row_counts
        .iter()
        .rposition(|&count| count >= min_row_pixels)? as u32;
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
            let alpha = sp[3] as u16;
            if alpha == 0 {
                continue;
            }
            if alpha == 255 {
                dest.put_pixel(dx as u32, dy as u32, Rgba(sp));
                continue;
            }
            let dp = dest.get_pixel(dx as u32, dy as u32).0;
            let inv = 255 - alpha;
            dest.put_pixel(
                dx as u32,
                dy as u32,
                Rgba([
                    ((sp[0] as u16 * alpha + dp[0] as u16 * inv + 127) / 255) as u8,
                    ((sp[1] as u16 * alpha + dp[1] as u16 * inv + 127) / 255) as u8,
                    ((sp[2] as u16 * alpha + dp[2] as u16 * inv + 127) / 255) as u8,
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

fn load_rgba(path: &str) -> Option<RgbaImage> {
    image::ImageReader::open(Path::new(path))
        .ok()?
        .with_guessed_format()
        .ok()?
        .decode()
        .ok()
        .map(|image| image.to_rgba8())
}

fn resize_rgba(image: &RgbaImage, width: u32, height: u32) -> Result<RgbaImage, String> {
    let mut destination = RgbaImage::new(width.max(1), height.max(1));
    let mut resizer = Resizer::new();
    let options = ResizeOptions::new()
        .resize_alg(ResizeAlg::Convolution(FastFilterType::Lanczos3))
        .use_alpha(true);
    resizer
        .resize(image, &mut destination, &options)
        .map_err(|error| format!("SIMD resize failed: {error}"))?;
    Ok(destination)
}

fn fit_image(
    image: &RgbaImage,
    cell_w: u32,
    cell_h: u32,
    card_type: &str,
) -> (RgbaImage, i64, i64, u32, u32) {
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
        let standard_width = ((cell_h as f32 * (477.0 / 677.0)).round() as u32).clamp(1, cell_w);
        nw = nw.max(standard_width);
    }
    let resized = resize_rgba(image, nw, nh).unwrap_or_else(|_| {
        DynamicImage::ImageRgba8(image.clone())
            .thumbnail_exact(nw, nh)
            .to_rgba8()
    });
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

fn source_revision(path: &str) -> (u64, u128) {
    fs::metadata(path)
        .ok()
        .map(|metadata| {
            let modified = metadata
                .modified()
                .ok()
                .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
                .map(|duration| duration.as_nanos())
                .unwrap_or(0);
            (metadata.len(), modified)
        })
        .unwrap_or((0, 0))
}

fn prepare_card(
    card: &CardInput,
    cell_w: u32,
    cell_h: u32,
    renderer_version: &str,
) -> Result<Arc<PreparedCard>, String> {
    let (source_len, source_mtime_ns) = source_revision(&card.path);
    let key = CardCacheKey {
        renderer_version: renderer_version.to_string(),
        path: card.path.clone(),
        source_len,
        source_mtime_ns,
        cell_w,
        cell_h,
        is_side: card.is_side,
        card_type: card.card_type.clone(),
    };
    if let Some(hit) = card_cache()
        .lock()
        .map_err(|_| "native card cache lock poisoned".to_string())?
        .get(&key)
        .cloned()
    {
        return Ok(hit);
    }

    let raw = load_rgba(&card.path)
        .ok_or_else(|| format!("card image is missing or unreadable: {}", card.path))?;
    let mut cropped = trim_visible_card(raw);
    if card.is_side {
        tint_side(&mut cropped);
    }
    let (cell, offset_x, offset_y, visible_w, visible_h) =
        fit_image(&cropped, cell_w, cell_h, &card.card_type);
    let prepared = Arc::new(PreparedCard {
        cell,
        offset_x,
        offset_y,
        visible_w,
        visible_h,
    });

    let mut cache = card_cache()
        .lock()
        .map_err(|_| "native card cache lock poisoned".to_string())?;
    if cache.len() >= card_cache_limit() {
        cache.clear();
    }
    cache.insert(key, Arc::clone(&prepared));
    Ok(prepared)
}

fn load_font(path: &str) -> Option<FontArc> {
    FontArc::try_from_vec(fs::read(path).ok()?).ok()
}

fn text_width(font: &FontArc, scale: PxScale, text: &str) -> i32 {
    let scaled = font.as_scaled(scale);
    let mut width = 0.0f32;
    let mut previous = None;
    for character in text.chars() {
        let glyph = scaled.glyph_id(character);
        if let Some(previous) = previous {
            width += scaled.kern(previous, glyph);
        }
        width += scaled.h_advance(glyph);
        previous = Some(glyph);
    }
    width.ceil() as i32
}

struct CenteredTextStyle {
    max_width: i32,
    start_size: f32,
    fill: Rgba<u8>,
    shadow: Rgba<u8>,
}

fn draw_centered_text(
    image: &mut RgbaImage,
    font: &FontArc,
    text: &str,
    center_x: i32,
    center_y: i32,
    style: CenteredTextStyle,
) {
    let mut size = style.start_size;
    while size > 28.0 {
        if text_width(font, PxScale::from(size), text) <= style.max_width {
            break;
        }
        size -= 4.0;
    }
    let scale = PxScale::from(size);
    let x = center_x - text_width(font, scale, text) / 2;
    let y = center_y - (size as i32 / 2);
    for dx in [-3, -2, -1, 1, 2, 3] {
        for dy in [-3, -2, -1, 1, 2, 3] {
            draw_text_mut(image, style.shadow, x + dx, y + dy, scale, font, text);
        }
    }
    draw_text_mut(image, style.fill, x, y, scale, font, text);
}

fn draw_title(
    image: &mut RgbaImage,
    title: &str,
    font: Option<&FontArc>,
    width: u32,
    top_margin: u32,
    style: ImageStyle,
    title_scale: f32,
) {
    if title.trim().is_empty() {
        return;
    }
    if let Some(font) = font {
        let (fill, shadow, base_size) = if style == ImageStyle::Parchment {
            (Rgba([48, 37, 28, 255]), Rgba([239, 207, 140, 220]), 98.0)
        } else {
            (Rgba([255, 255, 255, 255]), Rgba([20, 24, 30, 255]), 82.0)
        };
        draw_centered_text(
            image,
            font,
            title,
            (width / 2) as i32,
            (top_margin / 2) as i32,
            CenteredTextStyle {
                max_width: width as i32 - 120,
                start_size: base_size * title_scale,
                fill,
                shadow,
            },
        );
    }
}

fn make_x2_badge(width: u32, height: u32, font: Option<&FontArc>, style: ImageStyle) -> RgbaImage {
    let mut badge = RgbaImage::new(width.max(1), height.max(1));
    let Some(font) = font else {
        return badge;
    };
    let (fill, shadow, line) = if style == ImageStyle::Parchment {
        (
            Rgba([63, 39, 25, 255]),
            Rgba([224, 178, 87, 230]),
            Rgba([137, 89, 41, 205]),
        )
    } else {
        (
            Rgba([255, 255, 255, 255]),
            Rgba([20, 25, 32, 235]),
            Rgba([255, 255, 255, 218]),
        )
    };
    let scale = PxScale::from((height as f32 * 0.54).max(22.0));
    let text = "×2";
    let text_w = text_width(font, scale, text);
    let center_x = width as i32 / 2;
    let center_y = height as i32 / 2;
    let left_end = center_x - text_w / 2 - 10;
    let right_start = center_x + text_w / 2 + 10;
    let line_y = center_y + 3;
    if left_end > 8 {
        draw_line_segment_mut(
            &mut badge,
            (8.0, line_y as f32),
            (left_end as f32, line_y as f32),
            line,
        );
    }
    if right_start < width as i32 - 8 {
        draw_line_segment_mut(
            &mut badge,
            (right_start as f32, line_y as f32),
            ((width as i32 - 8) as f32, line_y as f32),
            line,
        );
    }
    draw_centered_text(
        &mut badge,
        font,
        text,
        center_x,
        center_y,
        CenteredTextStyle {
            max_width: width as i32,
            start_size: height as f32 * 0.54,
            fill,
            shadow,
        },
    );
    badge
}

fn draw_dust(
    image: &mut RgbaImage,
    dust_text: &str,
    dust_asset_path: &str,
    font: Option<&FontArc>,
    cards_bottom_y: u32,
    style: ImageStyle,
    mode: DustMode,
) {
    if mode == DustMode::Hidden {
        return;
    }
    let font = match font {
        Some(font) => font,
        None => return,
    };
    let scale_factor = if mode == DustMode::Large { 1.28 } else { 1.0 };
    let scale = PxScale::from(
        if style == ImageStyle::Parchment {
            132.0
        } else {
            108.0
        } * scale_factor,
    );
    let display_text = if style == ImageStyle::Parchment {
        dust_text
            .parse::<i64>()
            .map(|value| {
                let digits = value.to_string();
                let mut out = String::new();
                for (index, character) in digits.chars().rev().enumerate() {
                    if index > 0 && index % 3 == 0 {
                        out.push(' ');
                    }
                    out.push(character);
                }
                out.chars().rev().collect::<String>()
            })
            .unwrap_or_else(|_| dust_text.to_string())
    } else {
        dust_text.to_string()
    };
    let text_w = text_width(font, scale, &display_text).max(1);
    let text_h = (if style == ImageStyle::Parchment {
        126.0
    } else {
        106.0
    } * scale_factor) as i32;
    let mut dust = load_rgba(dust_asset_path)
        .map(trim_transparent)
        .unwrap_or_else(|| RgbaImage::new(1, 1));
    let dust_scale = if style == ImageStyle::Parchment {
        0.82
    } else {
        1.1
    };
    let dust_w = ((text_h as f32) * dust_scale).round().max(72.0) as u32;
    let dust_h =
        ((dust_w as f32) * dust.height() as f32 / dust.width().max(1) as f32).round() as u32;
    dust =
        resize_rgba(&dust, dust_w.max(1), dust_h.max(1)).unwrap_or_else(|_| RgbaImage::new(1, 1));
    let footer_h = image.height().saturating_sub(cards_bottom_y).max(1);
    let center_y = cards_bottom_y as i32 + ((footer_h as f32) * 0.6) as i32;
    let spacing = if style == ImageStyle::Parchment {
        12
    } else {
        15
    };
    let total_w = text_w + spacing + dust_w as i32;
    let start_x = (image.width() as i32 - total_w) / 2;
    let text_y = center_y - text_h / 2;
    overlay(
        image,
        &dust,
        (start_x + text_w + spacing) as i64,
        (center_y - dust_h as i32 / 2) as i64,
    );
    let shadow = if style == ImageStyle::Parchment {
        Rgba([239, 207, 140, 255])
    } else {
        Rgba([0, 0, 0, 255])
    };
    let fill = if style == ImageStyle::Parchment {
        Rgba([58, 39, 27, 255])
    } else {
        Rgba([255, 255, 255, 255])
    };
    for dx in [-3, -2, -1, 1, 2, 3] {
        for dy in [-3, -2, -1, 1, 2, 3] {
            draw_text_mut(
                image,
                shadow,
                start_x + dx,
                text_y + dy,
                scale,
                font,
                &display_text,
            );
        }
    }
    draw_text_mut(image, fill, start_x, text_y, scale, font, &display_text);
}

fn draw_mana_curve(
    image: &mut RgbaImage,
    cards: &[CardInput],
    cards_bottom_y: u32,
    font: Option<&FontArc>,
    style: ImageStyle,
    mode: ManaCurveMode,
    replacement_path: &str,
) {
    if mode == ManaCurveMode::Hidden {
        return;
    }
    let footer_h = image.height().saturating_sub(cards_bottom_y).max(1);
    let footer_margin = 36u32.max(((image.width() as f32) * 0.025) as u32);
    let footer_slot_w = 1u32.max(((image.width() as f32) * 0.35) as u32);
    let footer_slot_h = 1u32.max(((footer_h as f32) * 0.72) as u32);
    let footer_slot_y = cards_bottom_y + (footer_h.saturating_sub(footer_slot_h)) / 2;
    if mode == ManaCurveMode::Image {
        if let Some(replacement) = load_rgba(replacement_path) {
            let replacement = trim_transparent(replacement);
            let scale = ((footer_slot_w as f32 * 0.7) / replacement.width().max(1) as f32)
                .min((footer_slot_h as f32 * 0.7) / replacement.height().max(1) as f32);
            let width = ((replacement.width() as f32) * scale).round().max(1.0) as u32;
            let height = ((replacement.height() as f32) * scale).round().max(1.0) as u32;
            if let Ok(replacement) = resize_rgba(&replacement, width, height) {
                let x = footer_margin + footer_slot_w.saturating_sub(width) / 2;
                let y = footer_slot_y + footer_slot_h.saturating_sub(height) / 2;
                overlay(image, &replacement, x as i64, y as i64);
            }
        }
        return;
    }
    let mut curve = [0u32; 8];
    for card in cards {
        let bucket = if card.mana >= 7 {
            7
        } else {
            card.mana.max(0) as usize
        };
        curve[bucket] += card.count;
    }
    let max_count = curve.iter().copied().max().unwrap_or(1).max(1);
    let chart_w = 900u32.min(footer_slot_w);
    let chart_h = 380u32.min(
        ((footer_slot_h as f32)
            * if style == ImageStyle::Parchment {
                0.78
            } else {
                0.75
            }) as u32,
    );
    let chart_x = (footer_margin + (footer_slot_w.saturating_sub(chart_w)) / 2) as i32;
    let chart_y = (footer_slot_y + (footer_slot_h.saturating_sub(chart_h)) / 2) as i32;
    if style == ImageStyle::Classic {
        draw_filled_rect_mut(
            image,
            Rect::at(chart_x - 16, chart_y - 16).of_size(chart_w + 32, chart_h + 44),
            Rgba([6, 10, 16, 220]),
        );
    }
    let gap = if style == ImageStyle::Parchment {
        14u32
    } else {
        10u32
    };
    let bar_w = ((chart_w - gap * 7) / 8).max(10);
    let base_y = chart_y + chart_h as i32;
    if style != ImageStyle::Classic {
        let line = if style == ImageStyle::Parchment {
            Rgba([89, 55, 31, 220])
        } else {
            Rgba([244, 249, 255, 220])
        };
        draw_line_segment_mut(
            image,
            (chart_x as f32, base_y as f32),
            ((chart_x + chart_w as i32) as f32, base_y as f32),
            line,
        );
    }
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
        let color = if style == ImageStyle::Parchment {
            [
                64 + i as u8 * 2,
                154u8.saturating_sub(i as u8 * 10),
                211u8.saturating_sub(i as u8 * 9),
            ]
        } else {
            colors[i]
        };
        if bar_h > 0 {
            if style == ImageStyle::Custom {
                draw_filled_rect_mut(
                    image,
                    Rect::at(x0 - 2, y0 - 2).of_size(bar_w + 4, bar_h as u32 + 4),
                    Rgba([13, 18, 25, 210]),
                );
            }
            draw_filled_rect_mut(
                image,
                Rect::at(x0, y0).of_size(bar_w, bar_h as u32),
                Rgba([color[0], color[1], color[2], 238]),
            );
        }
        if let Some(font) = font {
            let label = if i == 7 {
                "7+".to_string()
            } else {
                i.to_string()
            };
            let scale = PxScale::from(36.0);
            let tx = x0 + bar_w as i32 / 2 - text_width(font, scale, &label) / 2;
            let label_color = if style == ImageStyle::Parchment {
                Rgba([58, 39, 27, 255])
            } else {
                Rgba([245, 249, 255, 255])
            };
            if style == ImageStyle::Custom {
                for dx in [-2, -1, 1, 2] {
                    for dy in [-2, -1, 1, 2] {
                        draw_text_mut(
                            image,
                            Rgba([13, 18, 25, 235]),
                            tx + dx,
                            base_y + 20 + dy,
                            scale,
                            font,
                            &label,
                        );
                    }
                }
            }
            draw_text_mut(image, label_color, tx, base_y + 20, scale, font, &label);
            if curve[i] > 0 && style != ImageStyle::Classic {
                let count = curve[i].to_string();
                let count_scale = PxScale::from(30.0);
                let count_x = x0 + bar_w as i32 / 2 - text_width(font, count_scale, &count) / 2;
                if style == ImageStyle::Custom {
                    for dx in [-2, -1, 1, 2] {
                        for dy in [-2, -1, 1, 2] {
                            draw_text_mut(
                                image,
                                Rgba([13, 18, 25, 235]),
                                count_x + dx,
                                y0 - 34 + dy,
                                count_scale,
                                font,
                                &count,
                            );
                        }
                    }
                }
                draw_text_mut(
                    image,
                    label_color,
                    count_x,
                    y0 - 34,
                    count_scale,
                    font,
                    &count,
                );
            }
        }
    }
}

fn draw_class_art(
    image: &mut RgbaImage,
    class_asset_path: &str,
    cards_bottom_y: u32,
    mode: ClassArtMode,
) {
    if let Some(class_img) = load_rgba(class_asset_path) {
        let class_img = trim_transparent(class_img);
        let footer_h = image.height().saturating_sub(cards_bottom_y).max(1);
        let footer_margin = 36u32.max(((image.width() as f32) * 0.025) as u32);
        let slot_w = 1u32.max(((image.width() as f32) * 0.35) as u32);
        let extra_height = ((footer_h as f32) * 0.125) as u32;
        let slot_h = footer_h.saturating_add(extra_height);
        let shift_x = 16u32.max(((image.width() as f32) * 0.0375) as u32);
        let slot_x = image
            .width()
            .saturating_sub(footer_margin)
            .saturating_sub(slot_w)
            .saturating_add(shift_x);
        let slot_y = cards_bottom_y.saturating_sub(extra_height);
        let max_fill = if mode == ClassArtMode::Logo {
            0.72
        } else {
            1.0
        };
        let scale = ((slot_w as f32 * max_fill) / class_img.width().max(1) as f32)
            .min((slot_h as f32 * max_fill) / class_img.height().max(1) as f32);
        let c_w = ((class_img.width() as f32) * scale).round().max(1.0) as u32;
        let c_h = ((class_img.height() as f32) * scale).round().max(1.0) as u32;
        let mut class_img =
            resize_rgba(&class_img, c_w, c_h).unwrap_or_else(|_| RgbaImage::new(1, 1));
        if mode == ClassArtMode::Class {
            for pixel in class_img.pixels_mut() {
                let mut p = pixel.0;
                p[0] = ((p[0] as f32) * 0.88) as u8;
                p[1] = ((p[1] as f32) * 0.88) as u8;
                p[2] = ((p[2] as f32) * 0.88) as u8;
                p[3] = ((p[3] as f32) * 0.85) as u8;
                *pixel = Rgba(p);
            }
        }
        overlay(
            image,
            &class_img,
            slot_x as i64 + (slot_w as i64 - class_img.width() as i64) / 2,
            slot_y as i64 + (slot_h as i64 - class_img.height() as i64) / 2,
        );
    }
}

fn draw_runes(image: &mut RgbaImage, runes: &[RuneInput]) {
    if runes.is_empty() {
        return;
    }
    let size = 200u32;
    let mut icons = Vec::new();
    for rune in runes {
        if let Some(icon) = load_rgba(&rune.path) {
            let icon = resize_rgba(&icon, size, size).unwrap_or_else(|_| RgbaImage::new(1, 1));
            for _ in 0..rune.count {
                icons.push(icon.clone());
            }
        }
    }
    if icons.is_empty() {
        return;
    }
    let total_width = size.saturating_mul(icons.len() as u32);
    let mut x = image.width().saturating_sub(total_width) / 2;
    let y = image.height().saturating_sub(210);
    for icon in icons {
        overlay(image, &icon, x as i64, y as i64);
        x = x.saturating_add(size);
    }
}

fn paste_resized_piece(
    target: &mut RgbaImage,
    source: &RgbaImage,
    source_box: (u32, u32, u32, u32),
    destination_box: (u32, u32, u32, u32),
) -> Result<(), String> {
    let source_piece = DynamicImage::ImageRgba8(source.clone())
        .crop_imm(
            source_box.0,
            source_box.1,
            source_box.2.saturating_sub(source_box.0),
            source_box.3.saturating_sub(source_box.1),
        )
        .to_rgba8();
    let destination_width = destination_box.2.saturating_sub(destination_box.0).max(1);
    let destination_height = destination_box.3.saturating_sub(destination_box.1).max(1);
    let piece = resize_rgba(&source_piece, destination_width, destination_height)?;
    overlay(
        target,
        &piece,
        destination_box.0 as i64,
        destination_box.1 as i64,
    );
    Ok(())
}

fn apply_wood_frame(
    image: &mut RgbaImage,
    path: &str,
    destination_slice: u32,
) -> Result<(), String> {
    let frame = load_rgba(path).ok_or_else(|| format!("wood frame is unreadable: {path}"))?;
    let (source_width, source_height) = frame.dimensions();
    if source_width < 27 || source_height < 27 {
        return Err("wood frame asset is too small".to_string());
    }
    let source_slice = 13u32;
    let (width, height) = image.dimensions();
    let destination_slice = destination_slice.clamp(12, width.min(height) / 4);
    let source_boxes = [
        (0, 0, source_slice, source_slice),
        (source_width - source_slice, 0, source_width, source_slice),
        (0, source_height - source_slice, source_slice, source_height),
        (
            source_width - source_slice,
            source_height - source_slice,
            source_width,
            source_height,
        ),
        (source_slice, 0, source_width - source_slice, source_slice),
        (
            source_slice,
            source_height - source_slice,
            source_width - source_slice,
            source_height,
        ),
        (0, source_slice, source_slice, source_height - source_slice),
        (
            source_width - source_slice,
            source_slice,
            source_width,
            source_height - source_slice,
        ),
    ];
    let destination_boxes = [
        (0, 0, destination_slice, destination_slice),
        (width - destination_slice, 0, width, destination_slice),
        (0, height - destination_slice, destination_slice, height),
        (
            width - destination_slice,
            height - destination_slice,
            width,
            height,
        ),
        (
            destination_slice,
            0,
            width - destination_slice,
            destination_slice,
        ),
        (
            destination_slice,
            height - destination_slice,
            width - destination_slice,
            height,
        ),
        (
            0,
            destination_slice,
            destination_slice,
            height - destination_slice,
        ),
        (
            width - destination_slice,
            destination_slice,
            width,
            height - destination_slice,
        ),
    ];
    for (source_box, destination_box) in source_boxes.into_iter().zip(destination_boxes) {
        paste_resized_piece(image, &frame, source_box, destination_box)?;
    }
    Ok(())
}

fn encode_jpeg(image: &RgbaImage, quality: u8) -> Result<Vec<u8>, String> {
    let rgb: ImageBuffer<Rgb<u8>, Vec<u8>> = DynamicImage::ImageRgba8(image.clone()).to_rgb8();
    let mut out = Vec::new();
    let mut cursor = Cursor::new(&mut out);
    JpegEncoder::new_with_quality(&mut cursor, quality)
        .encode_image(&DynamicImage::ImageRgb8(rgb))
        .map_err(|error| format!("jpeg encode failed: {error}"))?;
    Ok(out)
}

fn canonical_root(path: &str) -> ContractResult<PathBuf> {
    let path = Path::new(path);
    if !path.is_absolute() {
        return Err(format!("allowed root must be absolute: {path:?}"));
    }
    path.canonicalize()
        .map_err(|error| format!("allowed root is unavailable {path:?}: {error}"))
}

fn validate_asset_path(path: &str, allowed_roots: &[PathBuf], label: &str) -> ContractResult<()> {
    if path.is_empty() {
        return Err(format!("{label} is required"));
    }
    let candidate = Path::new(path);
    if !candidate.is_absolute() {
        return Err(format!("{label} must be an absolute path"));
    }
    let canonical = candidate
        .canonicalize()
        .map_err(|error| format!("{label} is unavailable: {error}"))?;
    if !allowed_roots.iter().any(|root| canonical.starts_with(root)) {
        return Err(format!("{label} is outside the allowed asset roots"));
    }
    if !canonical.is_file() {
        return Err(format!("{label} must point to a file"));
    }
    Ok(())
}

fn parse_render_input(payload: &Bound<'_, PyDict>) -> ContractResult<RenderInput> {
    let schema_version = get_i64(payload, "schema_version", 0)?;
    if schema_version != SCHEMA_VERSION {
        return Err(format!(
            "unsupported schema_version {schema_version}; expected {SCHEMA_VERSION}"
        ));
    }
    let renderer_version = get_string(payload, "renderer_version", "", 128)?;
    if renderer_version != RENDERER_VERSION {
        return Err(format!(
            "unsupported renderer_version {renderer_version:?}; expected {RENDERER_VERSION:?}"
        ));
    }

    let cards = parse_cards(payload)?;
    let layout_any = required_item(payload, "layout")?;
    let layout = layout_any
        .cast::<PyDict>()
        .map_err(|error| format!("layout must be a dictionary: {error}"))?;
    let assets_any = required_item(payload, "assets")?;
    let assets = assets_any
        .cast::<PyDict>()
        .map_err(|error| format!("assets must be a dictionary: {error}"))?;
    let output_any = required_item(payload, "output")?;
    let output = output_any
        .cast::<PyDict>()
        .map_err(|error| format!("output must be a dictionary: {error}"))?;
    let deck_any = required_item(payload, "deck")?;
    let deck = deck_any
        .cast::<PyDict>()
        .map_err(|error| format!("deck must be a dictionary: {error}"))?;
    let background_any = required_item(payload, "background")?;
    let background_dict = background_any
        .cast::<PyDict>()
        .map_err(|error| format!("background must be a dictionary: {error}"))?;
    let typography_any = required_item(payload, "typography")?;
    let typography = typography_any
        .cast::<PyDict>()
        .map_err(|error| format!("typography must be a dictionary: {error}"))?;
    let dust_any = required_item(payload, "dust")?;
    let dust = dust_any
        .cast::<PyDict>()
        .map_err(|error| format!("dust must be a dictionary: {error}"))?;
    let class_art_any = required_item(payload, "class_art")?;
    let class_art = class_art_any
        .cast::<PyDict>()
        .map_err(|error| format!("class_art must be a dictionary: {error}"))?;
    let mana_curve_any = required_item(payload, "mana_curve")?;
    let mana_curve = mana_curve_any
        .cast::<PyDict>()
        .map_err(|error| format!("mana_curve must be a dictionary: {error}"))?;

    let allowed_roots = parse_string_list(assets, "allowed_roots")?
        .iter()
        .map(|path| canonical_root(path))
        .collect::<ContractResult<Vec<_>>>()?;
    let water_path = get_string(assets, "water_path", "", 4096)?;
    let dust_asset_path = get_string(assets, "dust_asset_path", "", 4096)?;
    let class_asset_path = get_string(assets, "class_asset_path", "", 4096)?;
    let font_path = get_string(assets, "font_path", "", 4096)?;
    let ornament_font_path = get_string(assets, "ornament_font_path", "", 4096)?;
    let parchment_path = get_string(assets, "parchment_path", "", 4096)?;
    let wood_frame_path = get_string(assets, "wood_frame_path", "", 4096)?;
    let background_style =
        parse_image_style(&get_string(background_dict, "style", "classic", 32)?)?;
    let background_kind = get_string(background_dict, "kind", "", 32)?;
    let background_value = get_string(background_dict, "value", "", 128)?;
    let background_path = get_string(background_dict, "path", "", 4096)?;
    let background_blur = bounded_i64(background_dict, "blur", 0, 0, 100)? as u32;
    if background_style == ImageStyle::Custom
        && !matches!(background_kind.as_str(), "image" | "gradient" | "")
    {
        return Err(format!(
            "background.kind has unsupported value {background_kind:?}"
        ));
    }
    if background_style == ImageStyle::Custom && background_kind == "gradient" {
        let colors = background_value.split(',').collect::<Vec<_>>();
        if colors.len() != 2 {
            return Err("background.value must contain two #RRGGBB colors".to_string());
        }
        parse_hex_rgb(colors[0].trim())?;
        parse_hex_rgb(colors[1].trim())?;
    }
    let dust_mode = parse_dust_mode(&get_string(dust, "mode", "normal", 32)?)?;
    let class_art_mode = parse_class_art_mode(&get_string(class_art, "mode", "class", 32)?)?;
    let mana_curve_mode = parse_mana_curve_mode(&get_string(mana_curve, "mode", "chart", 32)?)?;
    let mana_curve_path = get_string(mana_curve, "path", "", 4096)?;
    let runes = parse_runes(payload)?;
    for (path, label) in [
        (&water_path, "assets.water_path"),
        (&dust_asset_path, "assets.dust_asset_path"),
        (&font_path, "assets.font_path"),
        (&ornament_font_path, "assets.ornament_font_path"),
        (&parchment_path, "assets.parchment_path"),
        (&wood_frame_path, "assets.wood_frame_path"),
    ] {
        validate_asset_path(path, &allowed_roots, label)?;
    }
    if !class_asset_path.is_empty() {
        validate_asset_path(&class_asset_path, &allowed_roots, "assets.class_asset_path")?;
    }
    if background_style == ImageStyle::Custom && background_kind == "image" {
        validate_asset_path(&background_path, &allowed_roots, "background.path")?;
    }
    if mana_curve_mode == ManaCurveMode::Image {
        validate_asset_path(&mana_curve_path, &allowed_roots, "mana_curve.path")?;
    }
    for (index, rune) in runes.iter().enumerate() {
        validate_asset_path(&rune.path, &allowed_roots, &format!("runes[{index}].path"))?;
    }
    for (index, card) in cards.iter().enumerate() {
        validate_asset_path(&card.path, &allowed_roots, &format!("cards[{index}].path"))?;
    }

    Ok(RenderInput {
        renderer_version,
        cards,
        cell_w: bounded_i64(layout, "cell_w", 375, 128, 1024)? as u32,
        cell_h: bounded_i64(layout, "cell_h", 507, 128, 1400)? as u32,
        row_gap: bounded_i64(layout, "row_gap", 72, 0, 512)? as u32,
        top_margin: bounded_i64(layout, "top_margin", 0, 0, 4096)? as u32,
        bottom_margin: bounded_i64(layout, "bottom_margin", 800, 1, 4096)? as u32,
        max_output_side: bounded_i64(output, "max_output_side", 1920, 256, 4096)? as u32,
        jpeg_quality: bounded_i64(output, "jpeg_quality", 92, 50, 100)? as u8,
        deck_cost: bounded_i64(deck, "cost", 0, 0, 1_000_000)?,
        n_cols: bounded_i64(layout, "n_cols", 0, 0, 20)? as u32,
        dust_asset_path,
        class_asset_path,
        font_path,
        ornament_font_path,
        parchment_path,
        wood_frame_path,
        background: BackgroundInput {
            style: background_style,
            kind: background_kind,
            value: background_value,
            path: background_path,
            blur: background_blur,
        },
        title_scale: bounded_f64(typography, "title_scale", 1.0, 0.5, 3.0)? as f32,
        dust_mode,
        class_art_mode,
        mana_curve_mode,
        mana_curve_path,
        runes,
        deck_name: get_optional_string(deck, "name", 256)?,
    })
}

fn render_deck_image_inner(input: RenderInput) -> Result<Vec<u8>, String> {
    let n_cards = input.cards.len() as u32;
    let cell_w = input.cell_w;
    let cell_h = input.cell_h;
    let automatic_cols = (3000 / cell_w).max(1);
    let n_cols = n_cards
        .min(if input.n_cols > 0 {
            input.n_cols
        } else {
            automatic_cols
        })
        .max(1);
    let n_rows = n_cards.div_ceil(n_cols);
    let width = n_cols
        .checked_mul(cell_w)
        .ok_or_else(|| "canvas width overflow".to_string())?;
    let row_height = cell_h
        .checked_add(input.row_gap)
        .ok_or_else(|| "row height overflow".to_string())?;
    let height = n_rows
        .checked_mul(row_height)
        .and_then(|value| value.checked_add(input.bottom_margin))
        .and_then(|value| value.checked_add(input.top_margin))
        .ok_or_else(|| "canvas height overflow".to_string())?;
    if width > MAX_CANVAS_SIDE
        || height > MAX_CANVAS_SIDE
        || u64::from(width) * u64::from(height) > MAX_CANVAS_PIXELS
    {
        return Err(format!(
            "canvas {width}x{height} exceeds native safety limits"
        ));
    }
    let mut canvas = make_background_cached(
        width,
        height,
        &input.background,
        &input.parchment_path,
        &input.renderer_version,
    )?;
    let font = load_font(&input.font_path);
    let ornament_font = load_font(&input.ornament_font_path);

    if let Some(title) = input.deck_name.as_ref() {
        draw_title(
            &mut canvas,
            title,
            font.as_ref(),
            width,
            input.top_margin,
            input.background.style,
            input.title_scale,
        );
    }

    let water_size = if n_cards <= 18 {
        (214, 121)
    } else if n_cards <= 32 {
        (141, 80)
    } else {
        (124, 70)
    };
    let water = make_x2_badge(
        water_size.0,
        water_size.1,
        ornament_font.as_ref().or(font.as_ref()),
        input.background.style,
    );

    let prepared_cards: Vec<Arc<PreparedCard>> = render_pool()?.install(|| {
        input
            .cards
            .par_iter()
            .map(|card| prepare_card(card, cell_w, cell_h, &input.renderer_version))
            .collect::<Result<Vec<_>, _>>()
    })?;

    let mut col = 0u32;
    let mut row = input.top_margin;
    for (card, prepared) in input.cards.iter().zip(prepared_cards.iter()) {
        overlay(&mut canvas, &prepared.cell, col as i64, row as i64);
        if card.count == 2 && water.width() > 1 && water.height() > 1 {
            let drop = 12i64.max(28i64.min((prepared.visible_h / 20) as i64));
            let mut wx = col as i64
                + prepared.offset_x
                + ((prepared.visible_w as i64 - water.width() as i64).max(0) / 2);
            let mut wy = row as i64 + prepared.offset_y + prepared.visible_h as i64 + drop;
            let next_row_y = row as i64 + cell_h as i64 + input.row_gap as i64;
            let max_wy = next_row_y - water.height() as i64 - 2;
            if wy > max_wy {
                wy = max_wy;
            }
            wy = wy.max(row as i64 + prepared.offset_y + prepared.visible_h as i64);
            wx = wx.max(0).min(canvas.width() as i64 - water.width() as i64);
            wy = wy
                .max(0)
                .min(canvas.height() as i64 - water.height() as i64);
            overlay(&mut canvas, &water, wx, wy);
        }
        col += cell_w;
        if col >= width {
            col = 0;
            row += cell_h + input.row_gap;
        }
    }

    let cards_bottom_y = n_rows * (cell_h + input.row_gap) + input.top_margin;
    draw_dust(
        &mut canvas,
        &input.deck_cost.to_string(),
        &input.dust_asset_path,
        if input.background.style == ImageStyle::Parchment {
            ornament_font.as_ref().or(font.as_ref())
        } else {
            font.as_ref()
        },
        cards_bottom_y,
        input.background.style,
        input.dust_mode,
    );
    draw_mana_curve(
        &mut canvas,
        &input.cards,
        cards_bottom_y,
        if input.background.style == ImageStyle::Parchment {
            ornament_font.as_ref().or(font.as_ref())
        } else {
            font.as_ref()
        },
        input.background.style,
        input.mana_curve_mode,
        &input.mana_curve_path,
    );
    if !input.class_asset_path.trim().is_empty() {
        draw_class_art(
            &mut canvas,
            &input.class_asset_path,
            cards_bottom_y,
            input.class_art_mode,
        );
    }
    draw_runes(&mut canvas, &input.runes);

    let mut decorative_padding = 0u32;
    if input.background.style == ImageStyle::Parchment {
        decorative_padding =
            36u32.max(((canvas.width().min(canvas.height()) as f32) * 0.012) as u32);
        let padded_width = canvas
            .width()
            .checked_add(decorative_padding.saturating_mul(2))
            .ok_or_else(|| "padded canvas width overflow".to_string())?;
        let padded_height = canvas
            .height()
            .checked_add(decorative_padding.saturating_mul(2))
            .ok_or_else(|| "padded canvas height overflow".to_string())?;
        if padded_width > MAX_CANVAS_SIDE
            || padded_height > MAX_CANVAS_SIDE
            || u64::from(padded_width) * u64::from(padded_height) > MAX_CANVAS_PIXELS
        {
            return Err(format!(
                "padded canvas {padded_width}x{padded_height} exceeds native safety limits"
            ));
        }
        let mut framed = parchment_canvas(padded_width, padded_height, &input.parchment_path)?;
        overlay(
            &mut framed,
            &canvas,
            decorative_padding as i64,
            decorative_padding as i64,
        );
        canvas = framed;
    }

    let mut output_scale = 1.0f32;
    if canvas.width() > input.max_output_side || canvas.height() > input.max_output_side {
        let scale = input.max_output_side as f32 / canvas.width().max(canvas.height()) as f32;
        output_scale = scale;
        let new_w = ((canvas.width() as f32) * scale).max(1.0) as u32;
        let new_h = ((canvas.height() as f32) * scale).max(1.0) as u32;
        canvas = render_pool()?.install(|| resize_rgba(&canvas, new_w, new_h))?;
    }
    if input.background.style == ImageStyle::Parchment {
        let frame_width = ((decorative_padding as f32) * output_scale) as u32;
        apply_wood_frame(
            &mut canvas,
            &input.wood_frame_path,
            frame_width.clamp(12, 46),
        )?;
    }

    encode_jpeg(&canvas, input.jpeg_quality)
}

#[pyfunction]
fn render_deck_image<'py>(
    py: Python<'py>,
    payload: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyBytes>> {
    let input = parse_render_input(payload).map_err(RenderContractError::new_err)?;
    let outcome =
        py.detach(move || catch_unwind(AssertUnwindSafe(|| render_deck_image_inner(input))));
    let bytes = match outcome {
        Ok(Ok(bytes)) => bytes,
        Ok(Err(error)) => return Err(NativeRenderError::new_err(error)),
        Err(_) => {
            return Err(NativeRenderError::new_err(
                "native renderer panic was contained",
            ));
        }
    };
    Ok(PyBytes::new(py, &bytes))
}

#[pyfunction]
fn renderer_info() -> PyResult<(String, usize, usize)> {
    let cached_cards = card_cache()
        .lock()
        .map_err(|_| PyValueError::new_err("native card cache lock poisoned"))?
        .len();
    Ok((
        "deckview_core/0.3.0".to_string(),
        configured_threads(),
        cached_cards,
    ))
}

#[pyfunction]
fn clear_card_cache() -> PyResult<()> {
    card_cache()
        .lock()
        .map_err(|_| PyValueError::new_err("native card cache lock poisoned"))?
        .clear();
    background_cache()
        .lock()
        .map_err(|_| PyValueError::new_err("native background cache lock poisoned"))?
        .clear();
    BACKGROUND_CACHE_HITS.store(0, Ordering::Relaxed);
    BACKGROUND_CACHE_MISSES.store(0, Ordering::Relaxed);
    BACKGROUND_CACHE_EVICTIONS.store(0, Ordering::Relaxed);
    Ok(())
}

#[pyfunction]
fn cache_info() -> PyResult<(usize, usize, usize, usize)> {
    let entries = background_cache()
        .lock()
        .map_err(|_| PyValueError::new_err("native background cache lock poisoned"))?
        .len();
    Ok((
        BACKGROUND_CACHE_HITS.load(Ordering::Relaxed),
        BACKGROUND_CACHE_MISSES.load(Ordering::Relaxed),
        BACKGROUND_CACHE_EVICTIONS.load(Ordering::Relaxed),
        entries,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gradient_has_requested_dimensions_and_opaque_alpha() {
        let gradient = make_gradient(31, 17).expect("test gradient must build");
        assert_eq!(gradient.dimensions(), (31, 17));
        assert!(gradient.pixels().all(|pixel| pixel.0[3] == 255));
    }

    #[test]
    fn fit_image_preserves_source_aspect_ratio() {
        let source = RgbaImage::from_pixel(200, 100, Rgba([1, 2, 3, 255]));
        let (_cell, offset_x, offset_y, visible_w, visible_h) =
            fit_image(&source, 300, 300, "SPELL");
        assert_eq!((visible_w, visible_h), (300, 150));
        assert_eq!((offset_x, offset_y), (0, 0));
    }

    #[test]
    fn location_frame_keeps_a_readable_minimum_width() {
        let source = RgbaImage::from_pixel(100, 300, Rgba([1, 2, 3, 255]));
        let (_cell, offset_x, offset_y, visible_w, visible_h) =
            fit_image(&source, 300, 300, "LOCATION");
        assert_eq!((visible_w, visible_h), (211, 300));
        assert_eq!((offset_x, offset_y), (44, 0));
    }

    #[test]
    fn cover_image_crops_extreme_banner_before_resize() {
        let source = RgbaImage::from_pixel(1_250, 123, Rgba([9, 8, 7, 255]));
        let covered = cover_image(&source, 1_800, 1_920).expect("cover resize must succeed");
        assert_eq!(covered.dimensions(), (1_800, 1_920));
        assert_eq!(*covered.get_pixel(900, 960), Rgba([9, 8, 7, 255]));
    }

    #[test]
    fn background_cache_hits_and_invalidates_on_source_revision() {
        let path = std::env::temp_dir().join(format!(
            "deckview-background-cache-{}.png",
            std::process::id()
        ));
        RgbaImage::from_pixel(8, 8, Rgba([10, 20, 30, 255]))
            .save(&path)
            .expect("test background must be writable");
        background_cache()
            .lock()
            .expect("test cache lock must be available")
            .clear();
        BACKGROUND_CACHE_HITS.store(0, Ordering::Relaxed);
        BACKGROUND_CACHE_MISSES.store(0, Ordering::Relaxed);
        let background = BackgroundInput {
            style: ImageStyle::Custom,
            kind: "image".to_string(),
            value: String::new(),
            path: path.to_string_lossy().into_owned(),
            blur: 0,
        };

        let first = make_background_cached(64, 48, &background, &background.path, "test/1")
            .expect("cold background render must succeed");
        let second = make_background_cached(64, 48, &background, &background.path, "test/1")
            .expect("warm background render must succeed");
        assert_eq!(first, second);
        assert_eq!(BACKGROUND_CACHE_MISSES.load(Ordering::Relaxed), 1);
        assert_eq!(BACKGROUND_CACHE_HITS.load(Ordering::Relaxed), 1);

        RgbaImage::from_pixel(9, 9, Rgba([200, 30, 40, 255]))
            .save(&path)
            .expect("updated test background must be writable");
        let third = make_background_cached(64, 48, &background, &background.path, "test/1")
            .expect("revised background render must succeed");
        assert_ne!(first.get_pixel(0, 0), third.get_pixel(0, 0));
        assert_eq!(BACKGROUND_CACHE_MISSES.load(Ordering::Relaxed), 2);
        let _ = fs::remove_file(path);
    }
}

#[pymodule]
fn deckview_core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add(
        "RenderContractError",
        module.py().get_type::<RenderContractError>(),
    )?;
    module.add(
        "NativeRenderError",
        module.py().get_type::<NativeRenderError>(),
    )?;
    module.add_function(wrap_pyfunction!(render_deck_image, module)?)?;
    module.add_function(wrap_pyfunction!(renderer_info, module)?)?;
    module.add_function(wrap_pyfunction!(clear_card_cache, module)?)?;
    module.add_function(wrap_pyfunction!(cache_info, module)?)?;
    Ok(())
}
