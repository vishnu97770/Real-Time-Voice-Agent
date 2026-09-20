from app.profiles import admissions, bank, insurance, telecom, underwriting
from app.profiles.base import Profile

# Adding a vertical means adding a profile module and listing it here.
# Nothing in app/session.py or app/brains changes.
PROFILES: dict[str, Profile] = {
    module.profile.id: module.profile
    for module in (underwriting, bank, insurance, telecom, admissions)
}


def get_profile(profile_id: str) -> Profile | None:
    return PROFILES.get(profile_id)
