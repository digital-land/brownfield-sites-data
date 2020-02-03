#!/usr/bin/env python3

#
#  normalise values by type defined in the schema
#  -- output is valid according to the 2019 guidance
#  -- log fixes as suggestions for the user to amend
#

import os
import sys
import re
import csv
import json
import validators
from datetime import datetime
from pyproj import Transformer
from decimal import Decimal

# convert from OSGB to WGS84
# https://epsg.io/27700
# https://epsg.io/4326
osgb_to_wgs84 = Transformer.from_crs(27700, 4326)

input_path = sys.argv[1]
output_path = sys.argv[2]
schema_path = sys.argv[3]
log_path = sys.argv[4]

resource = os.path.basename(os.path.splitext(input_path)[0])

schema = json.load(open(schema_path))
fields = {field["name"]: field for field in schema["fields"]}
fieldnames = [field["name"] for field in schema["fields"]]

organisation_uri = {}

log_fieldnames = ["row-number", "field", "datatype", "value"]
log_writer = csv.DictWriter(open(log_path, "w", newline=""), fieldnames=log_fieldnames)
log_writer.writeheader()
row_number = 0


def log_issue(field, datatype, value):
    log_writer.writerow(
        {"field": field, "datatype": datatype, "value": value, "row-number": row_number}
    )


def format_integer(value):
    return str(int(value))


def format_decimal(value, precision=6):
    return str(round(Decimal(value), precision).normalize())


def normalise_integer(field, value):
    value = normalise_integer.regex.sub("", value, 1)
    try:
        n = int(value)
    except Exception as e:
        log_issue(field, "integer", value)
        return ""
    return format_integer(n)


normalise_integer.regex = re.compile(r"\.0+$")


def normalise_decimal(field, value, precision):
    try:
        d = Decimal(value)
    except Exception as e:
        log_issue(field, "decimal", value)
        return ""
    return format_decimal(d)


def within_england(geox, geoy):
    return geoy > 49.5 and geoy < 56.0 and geox > -7.0 and geox < 2


def normalise_geometry(row):
    if row.get("GeoX", "") == "" or row.get("GeoY", "") == "":
        return row

    geox = Decimal(row["GeoX"])
    geoy = Decimal(row["GeoY"])

    row["GeoX"] = ""
    row["GeoY"] = ""

    if isinstance(geox, str) or isinstance(geoy, str):
        return row

    if within_england(geox, geoy):
        lon, lat = geox, geoy
    elif within_england(geoy, geox):
        lon, lat = geoy, geox
    else:
        lat, lon = osgb_to_wgs84.transform(geox, geoy)
        if not within_england(lon, lat):
            lat, lon = osgb_to_wgs84.transform(geoy, geox)
            if not within_england(lon, lat):
                log_issue("GeoX,GeoY", "OSGB", ",".join([row["GeoX"], row["GeoY"]]))
                return row

    row["GeoX"] = format_decimal(lon)
    row["GeoY"] = format_decimal(lat)
    return row


def lower_uri(value):
    return "".join(value.split()).lower()


def end_of_uri(value):
    return end_of_uri.regex.sub("", value.rstrip("/").lower())


end_of_uri.regex = re.compile(r".*/")


def load_organisations():
    organisation = {}
    for row in csv.DictReader(open("var/cache/organisation.csv", newline="")):
        organisation[row["organisation"]] = row
        if "opendatacommunities" in row:
            uri = row["opendatacommunities"].lower()
            organisation_uri[uri] = uri
            organisation_uri[end_of_uri(uri)] = uri
            organisation_uri[row["statistical-geography"].lower()] = uri
            if "local-authority-eng" in row["organisation"]:
                dl_url = "https://digital-land.github.io/organisation/%s/" % (
                    row["organisation"]
                )
                dl_url = dl_url.lower().replace("-eng:", "-eng/")
                organisation_uri[dl_url] = uri

    for row in csv.DictReader(open("patch/organisation.csv", newline="")):
        value = lower_uri(row["value"])
        if row["organisation"]:
            organisation_uri[value] = organisation[row["organisation"]][
                "opendatacommunities"
            ]


def normalise_organisation_uri(field, fieldvalue):
    value = lower_uri(fieldvalue)

    if value in organisation_uri:
        return organisation_uri[value]

    s = end_of_uri(value)
    if s in organisation_uri:
        return organisation_uri[s]

    log_issue(field, "opendatacommunities-uri", fieldvalue)
    return ""


def normalise_date(context, fieldvalue):
    value = fieldvalue.strip(' ",')

    # all of these patterns have been used!
    for pattern in [
        "%Y-%m-%d",
        "%Y%m%d",
        "%Y-%m-%dT%H:%M:%S.000Z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d",
        "%Y %m %d",
        "%Y.%m.%d",
        "%Y-%d-%m",  # risky!
        "%Y",
        "%Y.0",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d.%m.%y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%d %B %Y",
        "%b %d, %Y",
        "%b %d, %y",
        "%b-%y",
        "%m/%d/%Y",  # risky!
    ]:
        try:
            date = datetime.strptime(value, pattern)
            return date.strftime("%Y-%m-%d")
        except ValueError:
            pass

    log_issue(field, "date", fieldvalue)
    return ""


def normalise_uri(field, value):
    # some URIs have line-breaks and spaces
    uri = "".join(value.split())

    if validators.url(uri):
        return uri

    log_issue(field, "uri", value)
    return ""


def normalise(fieldname, value):
    if value.lower() in [None, "", "-", "n/a", "#n/a", "???", "<null>"]:
        return ""

    field = fields[fieldname]
    extras = field.get("digital-land", {})

    for strip in extras.get("strip", []):
        value = re.sub(strip, "", value)

    if fieldname == "OrganisationURI":
        return normalise_organisation_uri(fieldname, value)

    if field.get("type", "") == "integer":
        return normalise_integer(fieldname, value)

    if field.get("type", "") == "number":
        return normalise_decimal(fieldname, value, extras.get("precision", 6))

    if field.get("format", "") == "uri":
        return normalise_uri(fieldname, value)

    if field.get("type", "") == "date":
        return normalise_date(fieldname, value)

    return value


if __name__ == "__main__":
    reader = csv.DictReader(open(input_path, newline=""))

    load_organisations()

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in reader:
            row_number += 1
            o = {}
            for field in fieldnames:
                o[field] = normalise(field, row[field])

            o = normalise_geometry(o)

            writer.writerow(o)
