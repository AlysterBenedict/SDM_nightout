from flask import Flask, request, jsonify, send_from_directory
import pandas as pd
import requests
from bs4 import BeautifulSoup
import time
import tldextract
import re
from urllib.parse import urljoin, urlparse
import concurrent.futures
import os

app = Flask(__name__)

# Serve static files
@app.route('/<path:path>')
def serve_file(path):
    return send_from_directory('.', path)

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

# Generate Links API
@app.route('/generate_links', methods=['POST'])
def generate_links():
    data = request.json
    country = data.get('country')
    city = data.get('city')
    industry = data.get('industry')
    count = data.get('count', 20)
    max_pages = data.get('max_pages', 10)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    def extract_bing_links(html):
        soup = BeautifulSoup(html, "html.parser")
        results = soup.find_all("li", class_="b_algo")
        links = []
        for result in results:
            a_tag = result.find("a")
            if a_tag and a_tag.has_attr("href"):
                links.append(a_tag["href"])
        return links

    query = f"{industry} {city} {country}"
    encoded_query = query.replace(" ", "+")
    websites = set()
    page = 0

    while len(websites) < count and page < max_pages:
        url = f"https://www.bing.com/search?q={encoded_query}&first={page * 10 + 1}"
        try:
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                break

            links = extract_bing_links(response.text)
            prev_count = len(websites)
            for link in links:
                if link not in websites:
                    websites.add(link)
                if len(websites) >= count:
                    break

            if len(websites) == prev_count:
                break

        except Exception:
            break

        page += 1
        time.sleep(1)

    df = pd.DataFrame(list(websites), columns=["website"])
    df["search_query"] = query
    df.to_csv("actual_websites.csv", index=False)

    return jsonify({
        "websites": list(websites),
        "search_query": query
    })

# Filter Links API
@app.route('/filter_links', methods=['POST'])
def filter_links():
    def is_valid_url(url):
        try:
            parsed = urlparse(url)
            return parsed.scheme in ("http", "https")
        except:
            return False

    def get_domain_status(url):
        try:
            start = time.time()
            response = requests.get(url, timeout=5)
            load_time = round(time.time() - start, 2)
            return response.status_code, load_time
        except Exception as e:
            return str(e), None

    def detect_cms(url):
        try:
            response = requests.get(url, timeout=5)
            text = response.text.lower()

            if "wp-content" in text or "wordpress" in text:
                return "WordPress"
            if "shopify" in text:
                return "Shopify"
            if "drupal" in text:
                return "Drupal"
            if "joomla" in text:
                return "Joomla"
            if "squarespace" in text:
                return "Squarespace"
            if "wix.com" in text:
                return "Wix"

            return "Unknown"
        except:
            return "Error"

    df = pd.read_csv("actual_websites.csv")
    results = []

    for _, row in df.iterrows():
        url = row["website"]
        if not is_valid_url(url):
            continue

        status, speed = get_domain_status(url)
        cms = detect_cms(url)

        results.append({
            "website": url,
            "status": status,
            "cms": cms,
            "load_time_sec": speed
        })

    result_df = pd.DataFrame(results)
    result_df.to_csv("filtered_websites.csv", index=False)

    return jsonify({"websites": results})

# Save Valid Websites API
@app.route('/save_valid_websites', methods=['POST'])
def save_valid_websites():
    data = request.json
    max_load_time = data.get('max_load_time', 2.0)

    df = pd.read_csv("filtered_websites.csv")
    df['status'] = pd.to_numeric(df['status'], errors='coerce')
    df['load_time_sec'] = pd.to_numeric(df['load_time_sec'], errors='coerce')

    filtered_df = df[(df['status'] == 200) & (df['load_time_sec'] <= max_load_time)]
    filtered_df.to_csv("selected_websites.csv", index=False)

    return jsonify({"websites": filtered_df.to_dict('records')})

# Extract Emails API
@app.route('/extract_emails', methods=['POST'])
def extract_emails():
    EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
    PHONE_PATTERN = re.compile(r'\(?\+?\d{1,3}\)?[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{2,4}[\s.-]?\d{2,4}')
    SOCIAL_DOMAINS = ["facebook.com", "twitter.com", "instagram.com", "linkedin.com", "youtube.com", "t.me"]
    RELEVANT_SUBPAGES = ['contact', 'about', 'support', 'help', 'info', 'customer-service', 'faq']

    def get_domain(url):
        ext = tldextract.extract(url)
        return ext.domain + '.' + ext.suffix

    def extract_info_from_text(text):
        emails = set(re.findall(EMAIL_PATTERN, text))
        phones = set(re.findall(PHONE_PATTERN, text))
        return emails, phones

    def extract_social_links(soup):
        social_links = set()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag['href']
            for domain in SOCIAL_DOMAINS:
                if domain in href:
                    social_links.add(href)
        return social_links

    def fetch_page(url):
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            return resp.text
        except Exception:
            return None

    def scrape_info_from_url(url):
        html = fetch_page(url)
        if not html:
            return set(), set(), set()
        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text(separator=' ')
        emails, phones = extract_info_from_text(text)
        socials = extract_social_links(soup)
        return emails, phones, socials

    def extract_all_info_from_website(url):
        emails, phones, socials = scrape_info_from_url(url)
        if emails or phones or socials:
            return emails, phones, socials

        base_domain = get_domain(url)
        homepage_html = fetch_page(url)
        if not homepage_html:
            return set(), set(), set()

        soup = BeautifulSoup(homepage_html, 'html.parser')
        relevant_links = []
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href'].lower()
            full_url = urljoin(url, href)
            if get_domain(full_url) == base_domain:
                for keyword in RELEVANT_SUBPAGES:
                    if keyword in href:
                        relevant_links.append(full_url)
                        break

        all_emails, all_phones, all_socials = set(), set(), set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(scrape_info_from_url, link) for link in set(relevant_links)]
            for future in concurrent.futures.as_completed(futures):
                e, p, s = future.result()
                all_emails.update(e)
                all_phones.update(p)
                all_socials.update(s)

        return all_emails, all_phones, all_socials

    df = pd.read_csv("selected_websites.csv")
    results = []

    for _, row in df.iterrows():
        website = row['website']
        if not website.startswith(('http://', 'https://')):
            website = 'https://' + website

        emails, phones, socials = extract_all_info_from_website(website)
        results.append({
            'website': website,
            'emails': ', '.join(emails) if emails else 'no email',
            'phones': ', '.join(phones) if phones else 'no phone',
            'social_links': ', '.join(socials) if socials else 'no social links'
        })

    pd.DataFrame(results).to_csv("final_emails_output.csv", index=False)
    return jsonify(results)

if __name__ == '__main__':
    app.run(debug=True)
