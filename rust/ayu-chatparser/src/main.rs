//! A Rust port of `tg_chat_parser` (Python): turn a Telegram Desktop HTML chat
//! export into one TXT, Markdown or JSON file.
//!
//! The Python original drives BeautifulSoup with CSS selectors over the export
//! pages and renders three output formats. This port keeps the same selectors,
//! the same message model, the same attachment/reaction classification and the
//! same output layout, so the two produce equivalent files. The one part that
//! is not ported is speech recognition (`faster-whisper`): it is a large local
//! ML runtime with model downloads, not part of the export pipeline, and it is
//! kept behind the same seam the original uses, a pluggable transcriber.

use std::cell::RefCell;
use std::collections::{BTreeSet, HashMap};
use std::fmt;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::rc::Rc;

use scraper::{ElementRef, Html, Node, Selector};

/// One parsed message, mirroring the Python `Message` dataclass.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Message {
    pub id: String,
    pub date: String,
    pub sender: String,
    pub text: String,
    pub reply_to: String,
    pub reactions: Vec<String>,
}

/// The parse outcome, mirroring the Python `Result` dataclass.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Result_ {
    pub messages: Vec<Message>,
    pub warnings: Vec<String>,
    pub files: usize,
    pub duplicates: usize,
    pub audio: usize,
}

/// An un-read audio attachment awaiting transcription, so a cancelled item
/// keeps its original link in the message text.
///
/// Every `AudioItem` of one message shares a single `parts` handle, exactly as
/// the Python original shares one list per message; transcribing the second
/// voice message of a message therefore keeps the first one's edit.
#[derive(Debug, Clone)]
pub struct AudioItem {
    pub path: PathBuf,
    pub kind: &'static str,
    pub message_index: usize,
    pub parts: Rc<RefCell<Vec<String>>>,
    pub index: usize,
}

impl AudioItem {
    /// Replaces this item's placeholder in its message with transcribed text.
    pub fn set_text(&self, text: &str, messages: &mut [Message]) {
        let filled = if text.is_empty() {
            "тишина или неразборчиво".to_string()
        } else {
            text.to_string()
        };
        let mut parts = self.parts.borrow_mut();
        parts[self.index] = format!("[{}: {filled}]", self.kind);
        messages[self.message_index].text = parts.join("\n");
    }
}

/// The error taxonomy of the Python module: a missing export folder, an unsafe
/// media link, a wrong output extension, an existing output file, a missing
/// transcriber or an explicit cancellation.
#[derive(Debug)]
pub enum Error {
    /// `ValueError` from `export_folder`.
    BadExport(String),
    /// `ValueError` from `media_path`.
    BadMedia(String),
    /// `ValueError` from `save_result`.
    BadExtension(String),
    /// `FileExistsError` from `save_result`.
    Exists(PathBuf),
    /// `TranscriptionSetupError`: transcription requested but not available.
    TranscriptionSetup,
    /// `Cancelled`: the caller asked to stop.
    Cancelled,
    /// Any I/O failure, with the path for context.
    Io(PathBuf, std::io::Error),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::BadExport(m) => write!(f, "{m}"),
            Self::BadMedia(m) => write!(f, "{m}"),
            Self::BadExtension(m) => write!(f, "{m}"),
            Self::Exists(p) => write!(f, "Файл уже существует: {}", p.display()),
            Self::TranscriptionSetup => write!(
                f,
                "Для распознавания нужны зависимости из requirements-audio.txt."
            ),
            Self::Cancelled => write!(f, "Обработка отменена. Результат не записан."),
            Self::Io(p, e) => write!(f, "{}: {e}", p.display()),
        }
    }
}

impl std::error::Error for Error {}

/// A closure that recognizes one audio file, or an error to surface. Mirrors
/// the Python `Transcriber` protocol: `(path) -> text`.
pub type Transcriber<'a> = &'a dyn Fn(&Path) -> Result<String, String>;

/// A closure reporting whether the caller has cancelled.
pub type Cancel<'a> = &'a dyn Fn() -> bool;

/// A progress callback: `(done, total, status)`.
pub type Progress<'a> = &'a dyn Fn(f64, usize, &str);

// The selectors the Python module uses, parsed once. `scraper` shares the CSS
// selector subset BeautifulSoup's `select` uses, so the same strings apply.
fn selector(text: &str) -> Selector {
    Selector::parse(text).expect("static selector")
}

fn sel_message() -> Selector {
    selector("div.message.default")
}
fn sel_from_name() -> Selector {
    selector("div.from_name")
}
fn sel_date() -> Selector {
    selector("div.date.details")
}
fn sel_reply() -> Selector {
    selector("div.reply_to a[href]")
}
fn sel_media_link() -> Selector {
    selector("div.media_wrap a[href]")
}
fn sel_title() -> Selector {
    selector("div.title")
}
fn sel_unavailable_media() -> Selector {
    selector("div.media_wrap div.media")
}
fn sel_text() -> Selector {
    selector("div.text")
}
fn sel_reaction() -> Selector {
    selector("span.reactions span.reaction")
}
fn sel_emoji() -> Selector {
    selector("span.emoji")
}
fn sel_initials() -> Selector {
    selector("div.initials[title]")
}
fn sel_count() -> Selector {
    selector("span.count")
}

/// The natural sort key of a page file: the first run of digits in the stem,
/// then the whole name. Mirrors the Python `natural_key`.
///
/// The Python key is an arbitrary-precision `int`, so a digit run longer than
/// `u64` can hold still sorts numerically instead of collapsing to the `1`
/// default. The run is kept as digits with leading zeros stripped and compared
/// by length first, then lexicographically: for non-negative integers that is
/// exactly the numeric order, at any length. A stem without digits yields the
/// same `1` the Python default gives.
fn natural_key(path: &Path) -> (usize, String, String) {
    let stem = path
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let mut run = String::new();
    for ch in stem.chars() {
        if ch.is_ascii_digit() {
            run.push(ch);
        } else if !run.is_empty() {
            break;
        }
    }
    let digits = run.trim_start_matches('0');
    let (len, digits) = if digits.is_empty() {
        if run.is_empty() {
            (1, "1".to_string()) // no digits at all: the Python default
        } else {
            (1, "0".to_string()) // the run was all zeros
        }
    } else {
        (digits.len(), digits.to_string())
    };
    let name = path
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    (len, digits, name)
}

/// Resolves `source` to the export folder, or errors the way `export_folder`
/// does when there is no `messages*.html` in it.
pub fn export_folder(source: &Path) -> Result<PathBuf, Error> {
    let mut path = expand_and_normalize(source);
    if path.is_file()
        && path
            .extension()
            .map(|e| e.eq_ignore_ascii_case("html"))
            .unwrap_or(false)
    {
        path = path.parent().map(Path::to_path_buf).unwrap_or(path);
    }
    if !path.is_dir() || !has_messages_html(&path) {
        return Err(Error::BadExport(
            "Нужна папка HTML-экспорта Telegram с messages.html.".to_string(),
        ));
    }
    Ok(path)
}

fn has_messages_html(folder: &Path) -> bool {
    let Ok(entries) = fs::read_dir(folder) else {
        return false;
    };
    for entry in entries.flatten() {
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name.starts_with("messages") && name.to_ascii_lowercase().ends_with(".html") {
            return true;
        }
    }
    false
}

fn expand_and_normalize(path: &Path) -> PathBuf {
    let base = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    lexical_normalize(&base, path)
}

/// Joins `path` onto `base` (unless absolute) and resolves `.` and `..`
/// lexically, without touching the filesystem. This is `Path.resolve()`
/// with `strict=False` minus symlink resolution, which the export layout never
/// needs and which would require the path to exist.
fn lexical_normalize(base: &Path, path: &Path) -> PathBuf {
    let joined = if path.is_absolute() {
        path.to_path_buf()
    } else {
        base.join(path)
    };
    let mut out = PathBuf::new();
    for component in joined.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                if !out.pop() {
                    out.push("..");
                }
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

/// Decodes percent-escapes, mirroring `urllib.parse.unquote` for the ASCII and
/// UTF-8 cases the export uses. A malformed escape is left as written.
pub fn percent_decode(input: &str) -> String {
    let bytes = input.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let hi = hex_val(bytes[i + 1]);
            let lo = hex_val(bytes[i + 2]);
            if let (Some(hi), Some(lo)) = (hi, lo) {
                out.push((hi << 4) | lo);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn hex_val(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}

/// The pieces of a URL the parser cares about, from a `urlsplit` port.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct UrlParts {
    pub scheme: String,
    pub netloc: String,
    pub path: String,
    pub fragment: String,
}

/// A small `urlsplit` port. Only scheme, netloc, path and fragment are kept,
/// which is all `media_path` and the reply-link handling read.
pub fn split_url(input: &str) -> UrlParts {
    let mut rest = input;
    let mut fragment = String::new();
    if let Some(pos) = rest.find('#') {
        fragment = rest[pos + 1..].to_string();
        rest = &rest[..pos];
    }
    let rest = match rest.find('?') {
        Some(pos) => &rest[..pos],
        None => rest,
    };

    let mut scheme = String::new();
    let mut netloc = String::new();
    let mut path = rest.to_string();

    if let Some(colon) = scheme_end(rest) {
        scheme = rest[..colon].to_ascii_lowercase();
        let after = &rest[colon + 1..];
        // After a scheme, `//` starts an authority component.
        if let Some(authority) = after.strip_prefix("//") {
            let (netloc_part, path_part) = match authority.find('/') {
                Some(slash) => (&authority[..slash], &authority[slash..]),
                None => (authority, ""),
            };
            netloc = netloc_part.to_string();
            path = path_part.to_string();
        } else {
            path = after.to_string();
        }
    } else if let Some(authority) = rest.strip_prefix("//") {
        let (netloc_part, path_part) = match authority.find('/') {
            Some(slash) => (&authority[..slash], &authority[slash..]),
            None => (authority, ""),
        };
        netloc = netloc_part.to_string();
        path = path_part.to_string();
    }

    UrlParts {
        scheme,
        netloc,
        path,
        fragment,
    }
}

/// The index of the `:` that ends a URL scheme, if the prefix is a legal
/// scheme. `C:/secret.ogg` yields `Some(1)`, which is what makes it count as an
/// external link, exactly as `urlsplit` does.
fn scheme_end(input: &str) -> Option<usize> {
    let bytes = input.as_bytes();
    if bytes.is_empty() || !bytes[0].is_ascii_alphabetic() {
        return None;
    }
    for (i, &b) in bytes.iter().enumerate() {
        if b == b':' {
            return Some(i);
        }
        let ok = b.is_ascii_alphanumeric() || matches!(b, b'+' | b'-' | b'.');
        if !ok {
            return None;
        }
    }
    None
}

/// Resolves an attachment href to a path inside the export folder, refusing
/// anything that could read outside it. Mirrors `media_path`.
pub fn media_path(folder: &Path, href: &str) -> Result<PathBuf, Error> {
    let url = split_url(href);
    if !url.scheme.is_empty() || !url.netloc.is_empty() {
        return Err(Error::BadMedia("внешняя ссылка на медиа".to_string()));
    }
    let decoded = percent_decode(&url.path);
    let base = lexical_normalize(&std::env::current_dir().unwrap_or_default(), folder);
    let candidate = lexical_normalize(&base, Path::new(&decoded));
    if !candidate.starts_with(&base) {
        return Err(Error::BadMedia(
            "медиа за пределами папки экспорта".to_string(),
        ));
    }
    Ok(candidate)
}

fn classes_of(el: &ElementRef) -> Vec<String> {
    el.value().classes().map(str::to_string).collect()
}

fn attr(el: &ElementRef, name: &str) -> String {
    el.value().attr(name).unwrap_or("").to_string()
}

/// All descendant text with each node stripped and empty nodes dropped, then
/// joined by `sep`. This is exactly BeautifulSoup's `get_text(separator,
/// strip=True)`: it strips per text node and discards the ones that become
/// empty, rather than trimming the joined result.
fn text_joined(el: &ElementRef, sep: &str) -> String {
    let mut parts: Vec<String> = Vec::new();
    for node in el.descendants() {
        if let Node::Text(t) = node.value() {
            let piece = t.trim();
            if !piece.is_empty() {
                parts.push(piece.to_string());
            }
        }
    }
    parts.join(sep)
}

/// All descendant text with each node stripped and empty nodes dropped, then
/// concatenated. This is BeautifulSoup's `get_text(strip=True)`.
fn text_tight(el: &ElementRef) -> String {
    text_joined(el, "")
}

/// All descendant text with each `<br>` becoming a newline and the whole
/// trimmed, like `get_text()` after `br.replace_with("\n")`.
fn text_with_breaks(el: &ElementRef) -> String {
    let mut out = String::new();
    for node in el.descendants() {
        match node.value() {
            Node::Text(t) => out.push_str(t),
            Node::Element(e) if e.name() == "br" => out.push('\n'),
            _ => {}
        }
    }
    out.trim().to_string()
}

/// The index of the media link's parent `a[href]`, if the node sits inside one.
fn inside_anchor(node: &ElementRef) -> bool {
    node.ancestors().any(|a| {
        ElementRef::wrap(a)
            .is_some_and(|el| el.value().name() == "a" && el.value().attr("href").is_some())
    })
}

/// The options `parse_export` takes, mirroring the Python keyword arguments.
#[derive(Default)]
pub struct ParseOptions<'a> {
    pub transcribe: bool,
    pub transcriber: Option<Transcriber<'a>>,
    pub progress: Option<Progress<'a>>,
    pub cancel: Option<Cancel<'a>>,
    pub collect_audio: bool,
}

/// Parses the export into messages, warnings and counters.
///
/// Returns the messages plus any `AudioItem`s the caller asked to collect; the
/// items reference message indices, since a Rust `Vec` cannot hold a borrow of
/// an element it also owns.
pub fn parse_export(
    source: &Path,
    options: &ParseOptions<'_>,
) -> Result<(Result_, Vec<AudioItem>), Error> {
    let folder = export_folder(source)?;
    let noop_progress = |_: f64, _: usize, _: &str| {};
    let progress: Progress<'_> = match options.progress {
        Some(p) => p,
        None => &noop_progress,
    };
    let cancelled = || options.cancel.map(|c| c()).unwrap_or(false);

    let mut warnings: Vec<String> = Vec::new();
    let mut messages: Vec<Message> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut duplicates = 0usize;
    let mut audio = 0usize;
    let mut audio_cache: HashMap<PathBuf, String> = HashMap::new();
    let mut audio_items: Vec<AudioItem> = Vec::new();
    let mut sender = "Неизвестно".to_string();

    let pages = collect_pages(&folder)?;
    for (page_index, page) in pages.iter().enumerate() {
        if cancelled() {
            return Err(Error::Cancelled);
        }
        let name = page
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        progress(page_index as f64, pages.len(), &format!("Читаю {name}"));

        let html = fs::read_to_string(page).map_err(|e| Error::Io(page.clone(), e))?;
        let document = Html::parse_document(&html);
        let nodes: Vec<ElementRef> = document.select(&sel_message()).collect();

        for (index, node) in nodes.iter().enumerate() {
            if cancelled() {
                return Err(Error::Cancelled);
            }
            if let Some(author) = node.select(&sel_from_name()).next() {
                sender = text_joined(&author, " ");
            } else if !classes_of(node).iter().any(|c| c == "joined") {
                sender = "Неизвестно".to_string();
            }

            let message_id = attr(node, "id");
            if !message_id.is_empty() && seen.contains(&message_id) {
                duplicates += 1;
                continue;
            }
            if !message_id.is_empty() {
                seen.insert(message_id.clone());
            }

            let timestamp = node
                .select(&sel_date())
                .next()
                .map(|d| attr(&d, "title"))
                .unwrap_or_default();

            let reply_to = node
                .select(&sel_reply())
                .next()
                .map(|a| {
                    split_url(&attr(&a, "href"))
                        .fragment
                        .strip_prefix("go_to_")
                        .unwrap_or_default()
                        .to_string()
                })
                .unwrap_or_default();

            let mut parts: Vec<String> = Vec::new();
            let mut attachments: Vec<(PathBuf, &'static str, usize)> = Vec::new();
            let mut links: BTreeSet<String> = BTreeSet::new();

            for link in node.select(&sel_media_link()) {
                let href = attr(&link, "href");
                if links.contains(&href) {
                    continue;
                }
                links.insert(href.clone());
                let cls = classes_of(&link);
                let decoded = percent_decode(&split_url(&href).path);
                let title = link
                    .select(&sel_title())
                    .next()
                    .map(|t| text_joined(&t, " "))
                    .unwrap_or_default();

                if decoded.starts_with("voice_messages/")
                    || decoded.starts_with("round_video_messages/")
                {
                    audio += 1;
                    let kind = if decoded.starts_with("voice_messages/") {
                        "ГС"
                    } else {
                        "Кружок"
                    };
                    let mut label = format!("[{kind}: {href}]");
                    if options.transcribe || options.collect_audio {
                        match resolve_audio(
                            &folder,
                            &href,
                            kind,
                            &mut parts,
                            &mut attachments,
                            options,
                            progress,
                            &mut audio_cache,
                            page_index,
                            index,
                            nodes.len(),
                            pages.len(),
                        ) {
                            Ok(text) => {
                                if let Some(text) = text {
                                    label = format!("[{kind}: {text}]");
                                }
                            }
                            Err(Error::TranscriptionSetup) => {
                                return Err(Error::TranscriptionSetup);
                            }
                            Err(Error::BadMedia(reason)) => {
                                warnings.push(format!("{message_id}: {href}: {reason}"));
                                label = format!("[{kind}: {href} (ошибка распознавания)]");
                            }
                            Err(other) => {
                                warnings.push(format!("{message_id}: {href}: {other}"));
                                label = format!("[{kind}: {href} (ошибка распознавания)]");
                            }
                        }
                    }
                    parts.push(label);
                } else if cls.iter().any(|c| c == "photo_wrap") || decoded.starts_with("photos/") {
                    parts.push(format!("[Фото: {href}]"));
                } else if cls.iter().any(|c| c == "sticker_wrap")
                    || decoded.starts_with("stickers/")
                    || title.to_lowercase().contains("sticker")
                {
                    parts.push(format!("[Стикер: {href}]"));
                } else if cls.iter().any(|c| c == "animated_wrap") {
                    parts.push(format!("[GIF: {href}]"));
                } else if decoded.starts_with("video_files/") {
                    parts.push(format!("[Видео: {href}]"));
                } else {
                    parts.push(format!("[Файл: {href}]"));
                }
            }

            for unavailable in node.select(&sel_unavailable_media()) {
                if inside_anchor(&unavailable) {
                    continue;
                }
                let title = unavailable
                    .select(&sel_title())
                    .next()
                    .map(|t| text_joined(&t, " "))
                    .unwrap_or_else(|| "Медиа".to_string());
                parts.push(format!("[{title} (не скачано)]"));
            }

            if let Some(body) = node.select(&sel_text()).next() {
                let text = text_with_breaks(&body);
                if !text.is_empty() {
                    parts.push(text);
                }
            }

            let mut reactions: Vec<String> = Vec::new();
            for reaction in node.select(&sel_reaction()) {
                let Some(emoji) = reaction.select(&sel_emoji()).next() else {
                    continue;
                };
                let users: Vec<String> = reaction
                    .select(&sel_initials())
                    .map(|item| attr(&item, "title"))
                    .collect();
                let count = reaction.select(&sel_count()).next().map(|c| text_tight(&c));
                let suffix = if !users.is_empty() {
                    format!(" ({})", users.join(", "))
                } else if let Some(count) = count {
                    format!(" ×{count}")
                } else {
                    String::new()
                };
                reactions.push(format!("{}{suffix}", text_tight(&emoji)));
            }

            if timestamp.is_empty() {
                let who = if message_id.is_empty() {
                    "без id"
                } else {
                    message_id.as_str()
                };
                warnings.push(format!("{who}: отсутствует дата"));
            }
            if parts.is_empty() {
                parts.push("[Сообщение без текста или поддерживаемого медиа]".to_string());
            }

            messages.push(Message {
                id: message_id.clone(),
                date: timestamp,
                sender: sender.clone(),
                text: parts.join("\n"),
                reply_to,
                reactions,
            });

            if options.collect_audio && !attachments.is_empty() {
                let message_index = messages.len() - 1;
                // One shared list per message, as the Python original does.
                let shared = Rc::new(RefCell::new(parts));
                for (path, kind, part_index) in attachments {
                    audio_items.push(AudioItem {
                        path,
                        kind,
                        message_index,
                        parts: Rc::clone(&shared),
                        index: part_index,
                    });
                }
            }

            progress(
                page_index as f64 + (index + 1) as f64 / (nodes.len().max(1)) as f64,
                pages.len(),
                &format!("Обработано сообщений: {}", messages.len()),
            );
        }
    }

    if cancelled() {
        return Err(Error::Cancelled);
    }
    progress(
        pages.len() as f64,
        pages.len(),
        &format!("Готово: {} сообщений", messages.len()),
    );

    let result = Result_ {
        messages,
        warnings,
        files: pages.len(),
        duplicates,
        audio,
    };
    Ok((result, audio_items))
}

#[allow(clippy::too_many_arguments)]
fn resolve_audio(
    folder: &Path,
    href: &str,
    kind: &'static str,
    parts: &mut [String],
    attachments: &mut Vec<(PathBuf, &'static str, usize)>,
    options: &ParseOptions<'_>,
    progress: Progress<'_>,
    cache: &mut HashMap<PathBuf, String>,
    page_index: usize,
    index: usize,
    node_count: usize,
    pages_total: usize,
) -> Result<Option<String>, Error> {
    let path = media_path(folder, href)?;
    if !path.is_file() {
        return Err(Error::BadMedia("файл не найден".to_string()));
    }
    if options.collect_audio {
        attachments.push((path, kind, parts.len()));
        return Ok(None);
    }
    let name = path
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    progress(
        page_index as f64 + index as f64 / (node_count.max(1)) as f64,
        pages_total.max(1),
        &format!("Распознаю {name}"),
    );
    if let Some(text) = cache.get(&path) {
        return Ok(Some(text.clone()));
    }
    let Some(transcriber) = options.transcriber else {
        return Err(Error::TranscriptionSetup);
    };
    let text = transcriber(&path).unwrap_or_default();
    let text = if text.is_empty() {
        "тишина или неразборчиво".to_string()
    } else {
        text
    };
    cache.insert(path, text.clone());
    Ok(Some(text))
}

fn collect_pages(folder: &Path) -> Result<Vec<PathBuf>, Error> {
    let mut pages: Vec<PathBuf> = Vec::new();
    let entries = fs::read_dir(folder).map_err(|e| Error::Io(folder.to_path_buf(), e))?;
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name.starts_with("messages") && name.to_ascii_lowercase().ends_with(".html") {
            pages.push(path);
        }
    }
    pages.sort_by_key(|p| natural_key(p));
    Ok(pages)
}

/// Escapes user text for Markdown the way the Python `escape` does: HTML
/// entities first, then a backslash before every Markdown-significant byte.
fn escape_markdown(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '\\' | '`' | '*' | '_' | '{' | '}' | '[' | ']' | '(' | ')' | '#' | '+' | '.' | '!'
            | '|' | '-' => {
                out.push('\\');
                out.push(ch);
            }
            _ => out.push(ch),
        }
    }
    out
}

/// Renders the result as `txt`, `md` or `json`, mirroring `render`.
pub fn render(result: &Result_, format: &str) -> String {
    if format == "json" {
        return render_json(result);
    }
    let mut lookup: HashMap<&str, &Message> = HashMap::new();
    for message in &result.messages {
        if !message.id.is_empty() {
            lookup.insert(message.id.as_str(), message);
        }
    }
    let mut lines: Vec<String> = Vec::new();
    for message in &result.messages {
        let mut reply = String::new();
        if !message.reply_to.is_empty() {
            let excerpt = lookup
                .get(message.reply_to.as_str())
                .map(|target| {
                    let flat = target.text.replace('\n', " ");
                    flat.chars().take(60).collect::<String>()
                })
                .unwrap_or_else(|| message.reply_to.clone());
            reply = format!(" (ответ на: «{excerpt}»)");
        }
        let reactions = if message.reactions.is_empty() {
            String::new()
        } else {
            format!(" {{Реакции: {}}}", message.reactions.join("; "))
        };
        let header = format!("[{}] {}{}", message.date, message.sender, reply);
        if format == "md" {
            lines.push(format!(
                "**{}**\n\n{}",
                escape_markdown(&header),
                escape_markdown(&format!("{}{}", message.text, reactions))
            ));
        } else {
            lines.push(format!("{header}: {}{reactions}", message.text));
        }
    }
    let separator = if format == "md" { "\n\n" } else { "\n" };
    let mut out = lines.join(separator);
    if !lines.is_empty() {
        out.push('\n');
    }
    out
}

fn render_json(result: &Result_) -> String {
    let messages: Vec<serde_json::Value> = result
        .messages
        .iter()
        .map(|m| {
            serde_json::json!({
                "id": m.id,
                "date": m.date,
                "sender": m.sender,
                "text": m.text,
                "reply_to": m.reply_to,
                "reactions": m.reactions,
            })
        })
        .collect();
    let value = serde_json::json!({
        "messages": messages,
        "warnings": result.warnings,
        "files": result.files,
        "duplicates": result.duplicates,
        "audio": result.audio,
    });
    let mut text = serde_json::to_string_pretty(&value).unwrap_or_default();
    text.push('\n');
    text
}

/// Writes the rendered result to `output`, refusing a wrong extension and an
/// existing file unless `overwrite`. Mirrors `save_result`, including the
/// temp-file-in-target-dir step and the link-instead-of-rename to avoid
/// clobbering a file that appeared during processing.
pub fn save_result(
    result: &Result_,
    output: &Path,
    overwrite: bool,
    cancel: Option<&dyn Fn() -> bool>,
) -> Result<PathBuf, Error> {
    let path = expand_and_normalize(output);
    let format = path
        .extension()
        .map(|e| e.to_string_lossy().to_ascii_lowercase())
        .unwrap_or_default();
    if !matches!(format.as_str(), "txt" | "md" | "json") {
        return Err(Error::BadExtension(
            "Результат должен иметь расширение .txt, .md или .json.".to_string(),
        ));
    }
    if path.exists() && !overwrite {
        return Err(Error::Exists(path));
    }
    let content = render(result, &format);
    let parent = path.parent().map(Path::to_path_buf).unwrap_or_default();
    let temporary = parent.join(format!(".tg-parser-{}.tmp", std::process::id()));
    let write_result = (|| -> Result<(), Error> {
        fs::write(&temporary, content).map_err(|e| Error::Io(temporary.clone(), e))?;
        if cancel.map(|c| c()).unwrap_or(false) {
            return Err(Error::Cancelled);
        }
        if overwrite {
            fs::rename(&temporary, &path).map_err(|e| Error::Io(path.clone(), e))?;
        } else {
            // A hard link fails if the target exists, which is the point: a
            // file that appeared during processing is not overwritten.
            fs::hard_link(&temporary, &path).map_err(|_| Error::Exists(path.clone()))?;
        }
        Ok(())
    })();
    let _ = fs::remove_file(&temporary);
    write_result?;
    Ok(path)
}

/// A one-line summary, mirroring `summary`.
pub fn summary(result: &Result_) -> String {
    let participants: BTreeSet<&str> = result.messages.iter().map(|m| m.sender.as_str()).collect();
    format!(
        "Сообщений: {}; участников: {}; HTML-файлов: {}; аудио: {}; дублей пропущено: {}; предупреждений: {}",
        result.messages.len(),
        participants.len(),
        result.files,
        result.audio,
        result.duplicates,
        result.warnings.len()
    )
}
fn main() {
    std::process::exit(run());
}

fn run() -> i32 {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut source: Option<PathBuf> = None;
    let mut output: Option<PathBuf> = None;
    let mut overwrite = false;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-o" | "--output" => {
                i += 1;
                let Some(value) = args.get(i) else {
                    eprintln!("Ошибка: {} требует значение", args[i - 1]);
                    return 1;
                };
                output = Some(PathBuf::from(value));
            }
            "--overwrite" => overwrite = true,
            "--transcribe" => {
                eprintln!("Ошибка: распознавание речи в этой сборке не поддерживается.");
                return 1;
            }
            "--model" => {
                // Validated like the Python `choices=MODELS` even though the
                // value is only consumed by transcription, which this build
                // does not ship: silently accepting any string would be a lie.
                i += 1;
                let Some(value) = args.get(i) else {
                    eprintln!("Ошибка: --model требует значение");
                    return 1;
                };
                if value != "base" && value != "small" {
                    eprintln!("Ошибка: --model: недопустимое значение {value} (база: base, small)");
                    return 1;
                }
            }
            "--language" => {
                i += 1;
                if args.get(i).is_none() {
                    eprintln!("Ошибка: --language требует значение");
                    return 1;
                }
            }
            "--offline" => {
                // A flag, meaningful only with transcription.
            }
            "-h" | "--help" => {
                print_help();
                return 0;
            }
            other if other.starts_with('-') => {
                eprintln!("Ошибка: неизвестный аргумент {other}");
                return 1;
            }
            other => source = Some(PathBuf::from(other)),
        }
        i += 1;
    }

    let Some(source) = source else {
        // The Python original opens a Tk window here. This build is a plain CLI
        // with no GUI, so it says so instead of pretending to have launched one.
        eprintln!("Ошибка: нужна папка HTML-экспорта (или messages.html).");
        print_help();
        return 1;
    };

    let folder = match export_folder(&source) {
        Ok(folder) => folder,
        Err(e) => {
            eprintln!("Ошибка: {e}");
            return 1;
        }
    };
    let output = output.unwrap_or_else(|| folder.join("full_chat.txt"));
    if output.exists() && !overwrite {
        eprintln!("Ошибка: Результат уже существует. Укажи другое имя или --overwrite.");
        return 1;
    }

    // Mirrors the Python `progress` closure: a status is printed only when it
    // changes, and the per-message "Обработано …" ticker is suppressed so it
    // does not flood the terminal.
    let last_status = RefCell::new(String::new());
    let progress = |_done: f64, _total: usize, status: &str| {
        if !status.starts_with("Обработано") && *last_status.borrow() != status {
            println!("{status}");
            *last_status.borrow_mut() = status.to_string();
        }
    };
    let options = ParseOptions {
        progress: Some(&progress),
        ..ParseOptions::default()
    };
    let (result, _) = match parse_export(&folder, &options) {
        Ok(pair) => pair,
        Err(e) => {
            eprintln!("Ошибка: {e}");
            return 1;
        }
    };
    let path = match save_result(&result, &output, overwrite, None) {
        Ok(path) => path,
        Err(e) => {
            eprintln!("Ошибка: {e}");
            return 1;
        }
    };
    println!("{}", summary(&result));
    for warning in &result.warnings {
        eprintln!("Предупреждение: {warning}");
    }
    println!("Сохранено: {}", path.display());
    0
}

fn print_help() {
    println!(
        "ayu-chatparser: HTML-экспорт Telegram в TXT, Markdown или JSON.\n\
         Использование: ayu-chatparser <папка-экспорта> [-o <файл>] [--overwrite]\n\
         Расширение выходного файла задаёт формат: .txt, .md или .json."
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// A directory unique to one test.
    ///
    /// The name is not built from the wall clock: on Windows that clock ticks
    /// in ~100ns steps, so two tests that start within the same tick get the
    /// same directory. They then share a `messages.html`, `paths_and_invalid_input`
    /// finds an export where it expects none, and the run fails once in a while.
    /// A process-wide counter plus `create_dir` is unique by construction; the
    /// retry covers a directory left behind by an earlier run under a reused pid.
    fn tempdir() -> PathBuf {
        use std::sync::atomic::{AtomicU64, Ordering};
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let base = std::env::temp_dir();
        loop {
            let n = COUNTER.fetch_add(1, Ordering::Relaxed);
            let dir = base.join(format!("ayu-chatparser-test-{}-{n}", std::process::id()));
            match fs::create_dir(&dir) {
                Ok(()) => return dir,
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => panic!("cannot create {}: {e}", dir.display()),
            }
        }
    }

    fn page(folder: &Path, name: &str, text: &str) {
        fs::write(folder.join(name), text).unwrap();
    }

    fn message(number: u32, body: &str, sender: &str, extra: &str, joined: bool) -> String {
        let author = if sender.is_empty() {
            String::new()
        } else {
            format!("<div class=\"from_name\">{sender}</div>")
        };
        let joined = if joined { " joined" } else { "" };
        format!(
            "<div class=\"message default{joined}\" id=\"message{number}\">\
             <div class=\"date details\" title=\"19.11.2026 12:34:56 UTC+03:00\"></div>\
             {author}{extra}<div class=\"text\">{body}</div></div>"
        )
    }

    #[test]
    fn pages_replies_duplicates_and_joined_sender() {
        let folder = tempdir();
        page(
            &folder,
            "messages.html",
            &message(1, "Привет<br>мир <b>жирный</b>!", "Аня", "", false),
        );
        page(
            &folder,
            "messages2.html",
            &format!(
                "{}{}",
                message(1, "Привет", "Аня", "", false),
                message(
                    2,
                    "Ответ",
                    "",
                    "<div class=\"reply_to\"><a href=\"messages.html#go_to_message1\">reply</a></div>",
                    true
                )
            ),
        );
        page(
            &folder,
            "messages10.html",
            &message(3, "Конец", "Борис", "", false),
        );

        let (result, _) = parse_export(&folder, &ParseOptions::default()).unwrap();
        let ids: Vec<&str> = result.messages.iter().map(|m| m.id.as_str()).collect();
        assert_eq!(ids, ["message1", "message2", "message3"]);
        assert_eq!(result.messages[1].sender, "Аня");
        assert_eq!(result.messages[0].text, "Привет\nмир жирный!");
        assert_eq!(result.duplicates, 1);
        assert!(render(&result, "txt").contains("ответ на: «Привет мир жирный!»"));
        let json: serde_json::Value = serde_json::from_str(&render(&result, "json")).unwrap();
        assert_eq!(json["messages"][1]["reply_to"], "message1");
        let _ = fs::remove_dir_all(&folder);
    }

    #[test]
    fn media_reactions_and_audio_cache() {
        let folder = tempdir();
        let media = "<div class=\"media_wrap\"><a href=\"round_video_messages/a.mp4\"><div class=\"media\"><div class=\"title\">Video message</div></div></a></div>";
        let reactions = "<span class=\"reactions\"><span class=\"reaction\"><span class=\"emoji\">👍</span><div class=\"initials\" title=\"Аня\"></div></span></span>";
        fs::create_dir_all(folder.join("round_video_messages")).unwrap();
        fs::write(folder.join("round_video_messages/a.mp4"), b"test").unwrap();
        page(
            &folder,
            "messages.html",
            &format!(
                "{}{}",
                message(1, "", "Аня", &format!("{media}{reactions}"), false),
                message(2, "", "Аня", media, false)
            ),
        );

        let calls = std::cell::RefCell::new(Vec::new());
        let transcriber = |p: &Path| -> Result<String, String> {
            calls.borrow_mut().push(p.to_path_buf());
            Ok("тест речи".to_string())
        };
        let options = ParseOptions {
            transcribe: true,
            transcriber: Some(&transcriber),
            ..ParseOptions::default()
        };
        let (result, _) = parse_export(&folder, &options).unwrap();
        assert_eq!(calls.borrow().len(), 1);
        assert_eq!(result.audio, 2);
        assert_eq!(result.messages[0].text, "[Кружок: тест речи]");
        assert_eq!(result.messages[0].reactions, ["👍 (Аня)"]);
        let _ = fs::remove_dir_all(&folder);
    }

    #[test]
    fn audio_items_preserve_cancelled_attachment() {
        let folder = tempdir();
        let voice = folder.join("voice_messages");
        fs::create_dir_all(&voice).unwrap();
        for name in ["a.ogg", "b.ogg"] {
            fs::write(voice.join(name), b"").unwrap();
        }
        let media = "<div class=\"media_wrap\"><a href=\"voice_messages/a.ogg\">a</a><a href=\"voice_messages/b.ogg\">b</a></div>";
        page(
            &folder,
            "messages.html",
            &message(1, "Подпись", "Аня", media, false),
        );

        let options = ParseOptions {
            collect_audio: true,
            ..ParseOptions::default()
        };
        let (mut result, items) = parse_export(&folder, &options).unwrap();
        assert_eq!(items.len(), 2);
        items[1].set_text("Вторая запись", &mut result.messages);
        assert_eq!(
            result.messages[0].text,
            "[ГС: voice_messages/a.ogg]\n[ГС: Вторая запись]\nПодпись"
        );
        let _ = fs::remove_dir_all(&folder);
    }

    #[test]
    fn audio_items_of_one_message_share_their_parts() {
        // The Python original hands every AudioItem of a message the same
        // `parts` list, so transcribing the second one keeps the first edit.
        let folder = tempdir();
        let voice = folder.join("voice_messages");
        fs::create_dir_all(&voice).unwrap();
        for name in ["a.ogg", "b.ogg"] {
            fs::write(voice.join(name), b"").unwrap();
        }
        let media = "<div class=\"media_wrap\"><a href=\"voice_messages/a.ogg\">a</a><a href=\"voice_messages/b.ogg\">b</a></div>";
        page(
            &folder,
            "messages.html",
            &message(1, "Подпись", "Аня", media, false),
        );

        let options = ParseOptions {
            collect_audio: true,
            ..ParseOptions::default()
        };
        let (mut result, items) = parse_export(&folder, &options).unwrap();
        items[0].set_text("Первое", &mut result.messages);
        items[1].set_text("Второе", &mut result.messages);
        assert_eq!(
            result.messages[0].text,
            "[ГС: Первое]\n[ГС: Второе]\nПодпись"
        );
        // An empty transcription keeps the shared list but fills in the
        // original's silence placeholder.
        items[0].set_text("", &mut result.messages);
        assert_eq!(
            result.messages[0].text,
            "[ГС: тишина или неразборчиво]\n[ГС: Второе]\nПодпись"
        );
        let _ = fs::remove_dir_all(&folder);
    }

    #[test]
    fn missing_audio_and_unexported_media() {
        let folder = tempdir();
        let media = "<div class=\"media_wrap\"><a href=\"voice_messages/missing.ogg\">voice</a><div class=\"media\"><div class=\"title\">Photo</div></div></div>";
        page(
            &folder,
            "messages.html",
            &message(1, "подпись", "Аня", media, false),
        );
        let options = ParseOptions {
            transcribe: true,
            ..ParseOptions::default()
        };
        let (result, _) = parse_export(&folder, &options).unwrap();
        assert_eq!(result.warnings.len(), 1);
        assert!(result.messages[0].text.contains("Photo (не скачано)"));
        assert!(result.messages[0].text.contains("подпись"));
        let _ = fs::remove_dir_all(&folder);
    }

    #[test]
    fn paths_and_invalid_input() {
        let folder = tempdir();
        for href in [
            "../secret.ogg",
            "%2e%2e/secret.ogg",
            "https://example.com/a.ogg",
            "C:/secret.ogg",
        ] {
            assert!(
                media_path(&folder, href).is_err(),
                "expected rejection for {href}"
            );
        }
        // No messages*.html: the folder is not an export.
        assert!(parse_export(&folder, &ParseOptions::default()).is_err());
        let _ = fs::remove_dir_all(&folder);
    }

    #[test]
    fn cancellation_and_no_overwrite() {
        let folder = tempdir();
        page(
            &folder,
            "messages.html",
            &message(1, "текст", "Аня", "", false),
        );
        let (result, _) = parse_export(&folder, &ParseOptions::default()).unwrap();
        let output = folder.join("out.txt");
        save_result(&result, &output, false, None).unwrap();
        assert!(save_result(&result, &output, false, None).is_err());

        let cancel = || true;
        let options = ParseOptions {
            cancel: Some(&cancel),
            ..ParseOptions::default()
        };
        assert!(matches!(
            parse_export(&folder, &options),
            Err(Error::Cancelled)
        ));

        let before = fs::read(&output).unwrap();
        assert!(matches!(
            save_result(&result, &output, true, Some(&cancel)),
            Err(Error::Cancelled)
        ));
        assert_eq!(fs::read(&output).unwrap(), before);
        let leftovers: Vec<_> = fs::read_dir(&folder)
            .unwrap()
            .flatten()
            .filter(|e| e.file_name().to_string_lossy().starts_with(".tg-parser-"))
            .collect();
        assert!(leftovers.is_empty());
        let _ = fs::remove_dir_all(&folder);
    }

    #[test]
    fn forward_reply_and_markdown_escaping() {
        let folder = tempdir();
        page(
            &folder,
            "messages.html",
            &format!(
                "{}{}",
                message(
                    1,
                    "&lt;script&gt;*hi*",
                    "Аня",
                    "<div class=\"reply_to\"><a href=\"#go_to_message2\">reply</a></div>",
                    false
                ),
                message(2, "позже", "Аня", "", false)
            ),
        );
        let (result, _) = parse_export(&folder, &ParseOptions::default()).unwrap();
        assert!(render(&result, "txt").contains("ответ на: «позже»"));
        let markdown = render(&result, "md");
        assert!(markdown.contains("&lt;script&gt;"));
        assert!(markdown.contains(r"\*hi\*"));
        let _ = fs::remove_dir_all(&folder);
    }

    #[test]
    fn split_url_matches_the_python_shapes() {
        assert_eq!(
            split_url("messages.html#go_to_message1").fragment,
            "go_to_message1"
        );
        let external = split_url("https://example.com/a.ogg");
        assert_eq!(external.scheme, "https");
        assert_eq!(external.netloc, "example.com");
        assert_eq!(external.path, "/a.ogg");
        let drive = split_url("C:/secret.ogg");
        assert_eq!(drive.scheme, "c");
        let relative = split_url("../secret.ogg");
        assert!(relative.scheme.is_empty() && relative.netloc.is_empty());
        assert_eq!(relative.path, "../secret.ogg");
    }

    #[test]
    fn natural_key_orders_pages_numerically() {
        let mut names = vec![
            PathBuf::from("messages10.html"),
            PathBuf::from("messages.html"),
            PathBuf::from("messages2.html"),
        ];
        names.sort_by_key(|p| natural_key(p));
        assert_eq!(
            names,
            vec![
                PathBuf::from("messages.html"),
                PathBuf::from("messages2.html"),
                PathBuf::from("messages10.html"),
            ]
        );
    }

    #[test]
    fn natural_key_handles_digit_runs_wider_than_u64() {
        // `u64::MAX` is 18446744073709551615 (20 digits); the Python key is an
        // arbitrary-precision int, so a 25-digit run must still sort above a
        // 5-digit one instead of collapsing to the `1` default.
        let mut names = vec![
            PathBuf::from("messages9999999999999999999999999.html"),
            PathBuf::from("messages12345.html"),
        ];
        names.sort_by_key(|p| natural_key(p));
        assert_eq!(
            names,
            vec![
                PathBuf::from("messages12345.html"),
                PathBuf::from("messages9999999999999999999999999.html"),
            ]
        );

        // Leading zeros do not inflate the sort position: "007" is 7.
        let mut zeros = vec![
            PathBuf::from("messages10.html"),
            PathBuf::from("messages007.html"),
        ];
        zeros.sort_by_key(|p| natural_key(p));
        assert_eq!(
            zeros,
            vec![
                PathBuf::from("messages007.html"),
                PathBuf::from("messages10.html"),
            ]
        );

        // A stem without digits keeps the Python default of 1.
        assert_eq!(natural_key(&PathBuf::from("all.html")).0, 1);
    }

    #[test]
    fn render_json_uses_the_python_field_order() {
        let result = Result_ {
            messages: vec![Message {
                id: "message1".into(),
                date: "d".into(),
                sender: "s".into(),
                text: "t".into(),
                reply_to: String::new(),
                reactions: Vec::new(),
            }],
            warnings: Vec::new(),
            files: 1,
            duplicates: 0,
            audio: 0,
        };
        let json = render(&result, "json");
        let keys: Vec<&str> = ["messages", "warnings", "files", "duplicates", "audio"].to_vec();
        let mut last = 0;
        for key in keys {
            let at = json.find(&format!("\"{key}\"")).expect("key present");
            assert!(at > last, "{key} out of order");
            last = at;
        }
        assert!(json.ends_with("}\n"));
    }
}
