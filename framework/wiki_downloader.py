from db.config import FOLDER
from framework.http_session import get_http_session


def download_from_wiki(slug, name):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    session = get_http_session()

    # Список источников в порядке приоритета
    sources = [
        f"https://hearthstone.wiki.gg/wiki/{'_'.join(name.split())}",
        f"https://hearthstone.fandom.com/wiki/{'_'.join(name.split())}",
    ]

    for url in sources:
        try:
            response = session.get(url, headers=headers, timeout=10)
            if not response.ok:
                continue

            r = response.text
            img_url = None

            # Логика для wiki.gg (thumbnail или pi-image)
            if "wiki.gg" in url:
                if "pi-image-thumbnail" in r:
                    temp = r[r.index("pi-image-thumbnail") :]
                    if 'src="' in temp:
                        start = temp.index('src="') + 5
                        img_url = temp[start : temp.index('"', start)]

            # Логика для fandom (width="270")
            if not img_url and 'width="270"' in r:
                temp = r[: r.index('width="270"')]
                if "img" in temp and 'src="' in temp:
                    temp = temp[temp.rindex("img") :]
                    start = temp.index('src="') + 5
                    img_url = temp[start : temp.index('"', start)]

            if img_url:
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                # Очистка параметров ресайза для получения полного изображения
                if "/revision/latest" in img_url:
                    img_url = img_url.split("/revision/latest")[0]

                img_res = session.get(img_url, headers=headers, timeout=10)
                if img_res.ok and not img_res.content.startswith(b"<?xml"):
                    with open(f"{FOLDER}{slug}.png", "wb") as photo:
                        photo.write(img_res.content)
                    return True
        except Exception as e:
            print(f"Error downloading from {url}: {e}")

    return False
