#!/usr/bin/env python3
"""Gera o feed de produtos da ASETECH (formato Google/Meta RSS 2.0) a partir do site.

Lê o sitemap da loja Tray, extrai o dataLayer de cada página de produto e grava
apenas produtos ativos e disponíveis, com títulos limpos e rótulos para campanhas.

Uso: python3 feed/gerar_feed.py [saida.xml]
"""
import concurrent.futures
import html
import json
import re
import sys
import time
import urllib.request
from xml.sax.saxutils import escape

LOJA = "https://www.asetech.com.br"
SITEMAP = LOJA + "/loja/arquivos/1325437/sitemaps/sitemap_1.xml"
UA = "Mozilla/5.0 (compatible; ASETECH-FeedBot/1.0; +https://www.asetech.com.br)"
SELO_CERTIFICADO = "Certificado digital ICP-Brasil com emissão por videoconferência e suporte ASETECH."
SELO = "Licença oficial com nota fiscal. Entrega digital por e-mail e suporte na ativação."
GOOGLE_CATEGORIA_SOFTWARE = "313"  # Software > Software de computador

MARCAS = ["Microsoft", "Kaspersky", "Norton", "McAfee", "Bitdefender", "ESET", "Adobe", "Autodesk",
          "Corel", "Yubico", "Tanca", "Ragtech", "Avast", "Trend Micro", "Elgin", "C3tech", "Bematech", "Intel", "SanDisk", "PCYes"]
MICROSOFT = re.compile(r"office|windows|microsoft|sql server|power bi|power automate|onedrive|teams|visio|project|exchange|dynamics|azure|\bcal\b|365", re.I)
# A loja vende só licenças digitais e certificado digital; itens físicos ficam fora do feed
FISICO = re.compile(r"automa[cç][aã]o comercial|nobreak|leitor|impressora|yubikey|security key|gaveta|balan[cç]a|"
                    r"processador|teclado|mouse|monitor|notebook|placa|mem[oó]ria|\bssd\b|\bhd\b|cart[aã]o (micro|sd|f[ií]sico)|"
                    r"kit micro|tanca|ragtech|c3tech|elgin|sandisk|pcyes", re.I)
CERTIFICADO = re.compile(r"certificado|e-?cnpj|e-?cpf", re.I)
LINHAS = [
    ("certificado", CERTIFICADO),
    ("combo", re.compile(r"(365|office|windows).*(kaspersky|norton|mcafee)|(kaspersky|norton|mcafee).*(365|office)", re.I)),
    ("antivirus", re.compile(r"kaspersky|norton|mcafee|bitdefender|eset|antiv[ií]rus|\bedr\b", re.I)),
    ("servidor", re.compile(r"server|sql|\bcal\b|power bi|power automate|windows 365|e3|e5", re.I)),
    ("m365", re.compile(r"365|onedrive|teams|exchange|intune", re.I)),
    ("office", re.compile(r"office|visio|project", re.I)),
    ("windows", re.compile(r"windows", re.I)),
    ("design", re.compile(r"adobe|autodesk|autocad|corel|paintshop", re.I)),
]
INTERNO = re.compile(r"^(gerar nota|teste|pagamento|produto teste)", re.I)
LEGADO = re.compile(r"\b(2003|2007|2008|2010|2012|2013|2014|2016)\b")

# Linha de negócio por categoria da loja (custom_label_0)
B2B = re.compile(r"empresa|corporativo|server|sql|power bi|business|e3|e5|windows 365|edr|small office", re.I)


def baixar(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("iso-8859-1", errors="replace")


def urls_do_sitemap():
    xml = baixar(SITEMAP)
    return [u for u in re.findall(r"<loc>([^<]+)</loc>", xml)]


def urls_das_vitrines(urls):
    """Links da home e das categorias principais: pegam produtos novos antes de entrarem no sitemap."""
    vitrines = [LOJA + "/"] + [u for u in urls if u.replace(LOJA, "").strip("/").count("/") == 0]
    links = set()
    for v in vitrines:
        try:
            # parâmetro aleatório evita a versão em cache da vitrine
            html_vitrine = baixar(f"{v}?nc={time.time_ns()}")
            links.update(re.findall(r'href="(' + re.escape(LOJA) + r'/[^"#?]+/[^"#?]+)"', html_vitrine))
        except Exception:
            pass
    return links


def ler_produto(url):
    try:
        # parâmetro aleatório evita a página em cache (preço antigo)
        s = baixar(f"{url}?nc={time.time_ns()}")
    except Exception:
        return None
    i = s.find("dataLayer = [")
    if i < 0:
        return None
    try:
        d = json.JSONDecoder().raw_decode(s, i + len("dataLayer = "))[0][0]
    except (json.JSONDecodeError, IndexError):
        return None
    if d.get("pageCategory") != "Produto" or not d.get("idProduct"):
        return None
    desc = re.search(r'"@type":\s*"Product",\s*"description":\s*"(.*?)",\s*"name"', s, re.S)
    d["_descricao"] = desc.group(1) if desc else ""
    d["_url"] = url
    return d


def limpar_texto(t):
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def titulo(d):
    t = limpar_texto(d["nameProduct"])
    t = re.sub(r"\s*-\s*$", "", t)
    ref = (d.get("reference") or "").strip()
    if ref and ref.upper() not in t.upper() and re.fullmatch(r"[A-Z0-9]{3}-[0-9]{5,6}", ref):
        t = f"{t} - {ref}"
    return t[:150]


def descricao(d):
    corpo = limpar_texto(d["_descricao"])
    selo = SELO_CERTIFICADO if CERTIFICADO.search(d["nameProduct"]) else SELO
    texto = f"{selo} {corpo}" if corpo else selo
    return texto[:4900]


def marca(d):
    b = limpar_texto(d.get("brand") or "")
    if b and b.upper() != "ASETECH":
        return b
    nome = d["nameProduct"]
    if re.search(r"autocad|revit|3ds max|fusion 360", nome, re.I):
        return "Autodesk"
    for m in MARCAS:
        if m.lower() in nome.lower():
            return m
    return "Microsoft" if MICROSOFT.search(nome) else "ASETECH"


def tipo_produto(d):
    c = limpar_texto(d.get("category") or "")
    if c:
        return c
    partes = d["_url"].replace(LOJA, "").strip("/").split("/")
    return partes[-2].replace("-", " ").title() if len(partes) > 1 else "Software"


def faixa_preco(v):
    if v < 300:
        return "ate-300"
    if v < 700:
        return "300-700"
    if v < 1500:
        return "700-1500"
    return "1500-mais"


def item_xml(d):
    preco = float(d["price"] or d["priceSell"])
    venda = float(d["priceSell"] or d["price"])
    if venda > preco:
        preco = venda
    categoria = tipo_produto(d)
    nome_marca = marca(d)
    linha = "b2b" if B2B.search(categoria + " " + d["nameProduct"]) else "b2c"
    campos = [
        ("g:id", d["idProduct"]),
        ("g:title", titulo(d)),
        ("g:description", descricao(d)),
        ("g:link", d["_url"]),
        ("g:image_link", (d.get("urlImage") or "").replace("\\/", "/")),
        ("g:availability", "in stock"),
        ("g:condition", "new"),
        ("g:price", f"{preco:.2f} BRL"),
        ("g:brand", nome_marca),
        ("g:google_product_category", GOOGLE_CATEGORIA_SOFTWARE),
        ("g:product_type", categoria),
        ("g:custom_label_0", linha),
        ("g:custom_label_1", faixa_preco(venda)),
        ("g:custom_label_2", nome_marca.lower()),
        ("g:custom_label_3", "legado" if LEGADO.search(d["nameProduct"]) else "atual"),
    ]
    if venda < preco:
        campos.append(("g:sale_price", f"{venda:.2f} BRL"))
    ref = (d.get("reference") or "").strip()
    if ref:
        campos.append(("g:mpn", ref))
    else:
        campos.append(("g:identifier_exists", "no"))
    texto = categoria + " " + d["nameProduct"]
    campos.append(("g:custom_label_4", next((n for n, rx in LINHAS if rx.search(texto)), "outros")))
    corpo = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in campos if v)
    return f"<item>{corpo}</item>"


def main():
    saida = sys.argv[1] if len(sys.argv) > 1 else "feed/produtos.xml"
    urls = urls_do_sitemap()
    urls = urls + sorted(urls_das_vitrines(urls) - set(urls))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        produtos = [p for p in ex.map(ler_produto, urls) if p]
    vistos, itens = set(), []
    for d in produtos:
        if d["idProduct"] in vistos or d.get("availability") != "YES" or INTERNO.search(d["nameProduct"].strip()):
            continue
        if FISICO.search((d.get("category") or "") + " " + d["nameProduct"]):
            continue
        if not d.get("urlImage") or float(d.get("priceSell") or 0) <= 0:
            continue
        vistos.add(d["idProduct"])
        itens.append(item_xml(d))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0"><channel>'
        "<title>ASETECH Soluções - Licenças oficiais</title>"
        f"<link>{LOJA}</link><description>Catálogo de produtos ASETECH</description>"
        + "".join(itens)
        + "</channel></rss>"
    )
    with open(saida, "w", encoding="utf-8") as f:
        f.write(xml)
    print(f"{len(urls)} URLs no sitemap, {len(produtos)} páginas de produto, {len(itens)} itens no feed -> {saida}")


if __name__ == "__main__":
    main()
