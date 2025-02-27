# Copyright (c) Subramanya N. Licensed under the Apache License 2.0. All Rights Reserved
import logging
import os
import re
import tomllib

import ffmpy
import m3u8

import requests
from urllib.parse import urlparse

from download_echo360.course import Echo360Course
from download_echo360.downloader import Echo360Downloader

from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import mintotp
from urllib import parse

logging.basicConfig(
    format="[%(levelname)s: %(name)-12s] %(message)s",
    level=logging.ERROR)
_logger = logging.getLogger(__name__)

def start_download_binary(binary_downloader, binary_type):
    print("=" * 65)
    binary_downloader.download()
    _logger.info(f"Downloaded {binary_type} binary")
    print("=" * 65)

def run_setup_credentials(driver, url):
    # Load credentials from toml
    cred = tomllib.load(open("credentials.toml", "rb"))
    driver.get(url)

    wait = WebDriverWait(driver, 10)
    # Redirect to uvic login page
    wait.until(EC.element_to_be_clickable((By.ID, "email"))).send_keys(f'{cred["id"]}@{cred["domain"]}')
    wait.until(EC.element_to_be_clickable((By.ID, "submitBtn"))).click()

    # Fill uvic login page
    wait.until(EC.element_to_be_clickable((By.ID, "username"))).send_keys(cred['id'])
    wait.until(EC.element_to_be_clickable((By.ID, "password"))).send_keys(cred['password'])
    wait.until(EC.element_to_be_clickable((By.ID, "form-submit"))).click()

    # Complete Duo 2fa
    wait.until(EC.element_to_be_clickable((By.XPATH, "//a[text()='Other options']"))).click()
    wait.until(EC.element_to_be_clickable((By.XPATH, "//a/span/div[text()='Hardware token']"))).click()
    # Fill code
    code = mintotp.totp(cred['totp'])
    wait.until(EC.element_to_be_clickable((By.ID, "passcode-input"))).send_keys(code)
    wait.until(EC.element_to_be_clickable((By.XPATH, "//button[text()='Verify']"))).click()
    wait.until(EC.element_to_be_clickable((By.XPATH, "//button[text()='Yes, this is my device']"))).click()

    wait.until(EC.url_contains("https://echo360.ca/content#userIdentifier"))

    print("> Logged in successfully")
    print("-" * 80)


def main(course_urls, output_dir="download", course_hostname="", webdriver_to_use="chrome"):
    print("> Echo360 platform detected")
    print("> Please wait for Echo360 to load on SSO")
    print("-" * 80)

    if webdriver_to_use == "chrome":
        binary_type = "chromedriver"
        from download_echo360.download_binary.chromedriver import (
            ChromedriverDownloader as binary_downloader
        )
    
    binary_downloader = binary_downloader()
    _logger.info(
        f"Downloading {binary_downloader.get_download_link()[1]} binary to {binary_downloader.get_bin()}"
    )

    # check if the binary exists
    if not os.path.isfile(binary_downloader.get_bin()):
        start_download_binary(binary_downloader, binary_type)

    course_uuid = re.search(
            "[^/]([0-9a-zA-Z]+[-])+[0-9a-zA-Z]+", course_urls[0]
        ).group()
    
    course = Echo360Course(uuid=course_uuid, hostname=course_hostname)
    downloader = Echo360Downloader(course=course, output_dir=output_dir, webdriver_to_use=webdriver_to_use)

    _logger.info(
        '> Download will use {} webdriver'.format(webdriver_to_use)
    )

    # wait for user to login
    run_setup_credentials(driver=downloader._driver, url=course_hostname)

    session = requests.Session()
    for cookie in downloader._driver.get_cookies():
        session.cookies.set(cookie["name"], cookie["value"])

    for course_url in course_urls:
        parsed = parse.urlsplit(course_url)
        query = parse.parse_qs(parsed.query)
        access_id = query['secureLinkAccessDataId'][0]
        video_id = parsed.path.split("/")[-1]
        print("access id: "+access_id)
        print("video id: "+video_id)

        player_properties = session.get(f"https://echo360.ca/api/ui/echoplayer/secure-link-access-datas/{access_id}/media/{video_id}/player-properties").json()

        audio_url = player_properties["data"]["playableAudioVideo"]["playableMedias"][0]["uri"]
        video_url = player_properties["data"]["playableAudioVideo"]["playableMedias"][1]["uri"]
        url_pattern = player_properties["data"]["sourceQueryStrings"]["queryStrings"][0]["uriPattern"]
        query_string = player_properties["data"]["sourceQueryStrings"]["queryStrings"][0]["queryString"]
        file_name = player_properties["data"]["mediaName"]

        base_url = video_url.split("/1/")[0]+"/1/"

        def download_source(url):
            playlist = m3u8.loads(session.get(url).text)

            print("\nPARSED PLAYLIST\n")

            variant_url = f"{base_url}{playlist.playlists[1].uri}?{query_string}"
            print(variant_url)
            variant = m3u8.loads(session.get(variant_url).text)

            segments = variant.data["segments"]
            segments.insert(0, segments[0]["init_section"])

            size = segments[-1]['byterange'].split("@")[1]
            frag_bytes = bytes()
            for segment in segments:
                segment_url = f"{base_url}{segment['uri']}?{query_string}"
                [length, start] = segment['byterange'].split("@")
                end = int(start) + int(length) - 1
                print(f"{round(float(start)/float(size)*100)}% downloaded ({start}/{size} bytes)")
                frag_bytes += session.get(segment_url, headers={'Range': f"bytes={start}-{end}"}).content

            return frag_bytes

        print("Downloading audio")
        with open("frag_audio.mp3", "wb") as out:
            out.write(download_source(audio_url))

        print("Downloading video")
        with open("frag_video.mp4", "wb") as out:
            out.write(download_source(video_url))

        print("Merging sources")
        ff = ffmpy.FFmpeg(
            global_options="-loglevel panic",
            inputs={"frag_video.mp4": None, "frag_audio.mp3": None},
            outputs={file_name: ["-c", "copy"]},
        )
        ff.run()
        os.remove("frag_video.mp4")
        os.remove("frag_audio.mp3")
