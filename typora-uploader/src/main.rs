use std::env;
use std::fs;
use std::io::Read;
use std::path::Path;
use std::time::SystemTime;

const DEFAULT_SERVER: &str = "http://127.0.0.1:8765";

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("typora-uploader: usage: typora-uploader <image-path-or-url> [...]");
        std::process::exit(1);
    }

    let server = env::var("TYPORA_SERVER_URL")
        .unwrap_or_else(|_| DEFAULT_SERVER.to_string())
        .trim_end_matches('/')
        .to_string();
    let upload_url = format!("{}/upload", server);

    for raw in &args[1..] {
        let resolved = raw
            .replace("${filename}", "")
            .replace("${filepath}", "");
        match upload_single(&upload_url, &resolved) {
            Ok(url) => println!("{url}"),
            Err(e) => {
                eprintln!("Error uploading {raw}: {e}");
                std::process::exit(1);
            }
        }
    }
}

fn is_url(s: &str) -> bool {
    s.starts_with("http://") || s.starts_with("https://")
}

fn extract_filename(url: &str, content_disposition: Option<&str>) -> String {
    if let Some(cd) = content_disposition {
        for part in cd.split(';') {
            let part = part.trim();
            if let Some(name) = part.strip_prefix("filename=") {
                let name = name.trim_matches('"').trim_matches('\'');
                if !name.is_empty() {
                    return name.to_string();
                }
            }
        }
    }
    let path = url.split('?').next().unwrap_or(url);
    if let Some(name) = path.rsplit('/').next() {
        if !name.is_empty() {
            return name.to_string();
        }
    }
    format!(
        "img_{}.png",
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis()
    )
}

fn acquire_image(source: &str) -> Result<(Vec<u8>, String), Box<dyn std::error::Error>> {
    if is_url(source) {
        let resp = ureq::get(source).call()?;
        let cd = resp.header("content-disposition").map(|v| v.to_string());
        let filename = extract_filename(source, cd.as_deref());
        let mut data = Vec::new();
        resp.into_reader().read_to_end(&mut data)?;
        Ok((data, filename))
    } else {
        let path = Path::new(source);
        if !path.exists() {
            return Err(format!("file not found: {source}").into());
        }
        let data = fs::read(path)?;
        let filename = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("image.png")
            .to_string();
        Ok((data, filename))
    }
}

fn upload_single(upload_url: &str, source: &str) -> Result<String, Box<dyn std::error::Error>> {
    let (data, filename) = acquire_image(source)?;

    let boundary = "----TyporaUploaderBoundary";
    let mut body: Vec<u8> = Vec::new();

    body.extend_from_slice(format!("--{boundary}\r\n").as_bytes());
    body.extend_from_slice(
        format!(
            "Content-Disposition: form-data; name=\"image\"; filename=\"{filename}\"\r\n"
        )
        .as_bytes(),
    );
    body.extend_from_slice(b"Content-Type: application/octet-stream\r\n\r\n");
    body.extend_from_slice(&data);
    body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());

    let content_type = format!("multipart/form-data; boundary={boundary}");

    let resp = ureq::post(upload_url)
        .set("Content-Type", &content_type)
        .send_bytes(&body)?;

    let status = resp.status();
    let text = resp.into_string()?;

    if status != 200 {
        return Err(format!("server returned HTTP {status}").into());
    }

    let json: serde_json::Value = serde_json::from_str(&text)?;

    let ok = json
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    if ok {
        if let Some(results) = json.get("results").and_then(|v| v.as_array()) {
            if let Some(entry) = results.first() {
                if let Some(url_str) = entry.get("url").and_then(|v| v.as_str()) {
                    return Ok(url_str.to_string());
                }
            }
        }
    }

    Err(format!("upload failed: {text}").into())
}
