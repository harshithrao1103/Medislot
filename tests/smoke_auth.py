
"""Live MediSlot authentication smoke tests.

Set MEDISLOT_API_BASE_URL without a trailing slash.
"""

import json
import os
import secrets
import urllib.error
import urllib.request

BASE = os.environ["MEDISLOT_API_BASE_URL"].rstrip("/")


def call(path, method="POST", body=None, access=None):
    headers = {"Content-Type": "application/json"}

    if access:
        headers["Authorization"] = "Bearer " + access

    payload = json.dumps(body).encode("utf-8") if body is not None else None

    req = urllib.request.Request(
        BASE + path,
        data=payload,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return response.status, json.loads(response.read())

    except urllib.error.HTTPError as ex:
        return ex.code, json.loads(ex.read())


suffix = secrets.token_hex(6)
password = "Milestone2Secure123"

# Fetch valid specialization IDs from the deployed API.
status, catalog = call("/specializations", method="GET")

assert status == 200, ("specializations", status, catalog)

specializations = catalog["specializations"]

general_medicine = next(
    (
        item["specialization_id"]
        for item in specializations
        if item["specialization_name"] == "General Medicine"
    ),
    None,
)

assert general_medicine is not None, (
    "General Medicine specialization not found"
)

print("Specializations fetched successfully")


for role in ("doctors", "patients"):

    body = {
        "name": "CI Test",
        "email": f"m2-{role}-{suffix}@example.invalid",
        "password": password,
    }

    if role == "doctors":
        body.update({
            "licence_number": f"MED-2026-{secrets.randbelow(1000000):06d}",
            "specialization_id": general_medicine,
            "experience_years": 5,
            "qualifications": "MBBS, MD",
            "hospital_name": "MediSlot Test Hospital",
            "clinic_address": "Test Clinic, Hyderabad",
            "city": "Hyderabad",
            "languages": ["English", "Telugu"],
            "timezone": "Asia/Kolkata",
        })

    else:
        body.update({
            "date_of_birth": "2000-01-15",
            "gender": "Male",
            "phone": "9876543210",
            "city": "Hyderabad",
            "emergency_contact": "9876543211",
            "notes": "CI smoke test patient",
        })

    # 1. Register user and profile together
    status, data = call(
        f"/auth/{role}/register",
        body=body,
    )

    assert status == 201, (role, "registration", status, data)
    assert "userId" in data, (role, "missing userId", data)

    print(f"{role}: Registration successful")

    # 2. Login
    status, data = call(
        f"/auth/{role}/login",
        body={
            "email": body["email"],
            "password": password,
        },
    )

    assert status == 200, (role, "login", status, data)
    assert "accessToken" in data, (role, "missing access token")
    assert "refreshToken" in data, (role, "missing refresh token")

    access = data["accessToken"]
    refresh = data["refreshToken"]

    print(f"{role}: Login successful")

    # 3. Role-based authorization
    status, _ = call(
        "/doctor-auth-check",
        method="GET",
        access=access,
    )

    expected_status = 200 if role == "doctors" else 403

    assert status == expected_status, (
        role, "authorization", status
    )

    print(f"{role}: Role authorization successful")

    # 4. Tampered JWT must be rejected
    status, _ = call(
        "/doctor-auth-check",
        method="GET",
        access=access + "tampered",
    )

    assert status == 401, (
        role, "tampered token", status
    )

    print(f"{role}: Tampered token rejected")

    # 5. Refresh: issue a new access token only
    status, refreshed = call(
        "/auth/refresh",
        body={"refreshToken": refresh},
    )

    assert status == 200, (
        role, "first refresh", status, refreshed
    )

    assert "accessToken" in refreshed, (
        role, "missing refreshed access token"
    )

    # The API must not rotate the refresh token.
    assert refreshed.get("refreshToken", refresh) == refresh, (
        role, "refresh token was rotated"
    )

    new_access = refreshed["accessToken"]

    print(f"{role}: First refresh successful")

    # 6. Same refresh token must remain reusable
    status, refreshed_again = call(
        "/auth/refresh",
        body={"refreshToken": refresh},
    )

    assert status == 200, (
        role, "second refresh", status, refreshed_again
    )

    assert "accessToken" in refreshed_again, (
        role, "second refresh missing access token"
    )

    assert refreshed_again.get("refreshToken", refresh) == refresh, (
        role, "refresh token changed"
    )

    print(f"{role}: Existing refresh token remains valid")

    # 7. Logout using original refresh token
    status, data = call(
        "/auth/logout",
        body={"refreshToken": refresh},
        access=new_access,
    )

    assert status == 200, (
        role, "logout", status, data
    )

    print(f"{role}: Logout successful")

    # 8. JWT must be revoked after logout
    status, _ = call(
        "/doctor-auth-check",
        method="GET",
        access=new_access,
    )

    assert status == 401, (
        role, "revoked access token", status
    )

    # 9. Refresh token must be invalid after logout
    status, _ = call(
        "/auth/refresh",
        body={"refreshToken": refresh},
    )

    assert status == 401, (
        role, "refresh token after logout", status
    )

    print(f"{role}: Tokens revoked successfully")


print(
    "Live authentication smoke tests passed: "
    "registration, login, role authorization, "
    "JWT tampering, refresh reuse, and logout."
)
