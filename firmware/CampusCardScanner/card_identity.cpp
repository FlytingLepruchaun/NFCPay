#include "card_identity.h"

#include <string.h>

#include "config.h"

bool readCardIdentity(const char* uid, char* rollNumber, size_t rollNumberSize) {
  if (uid == nullptr || rollNumber == nullptr || rollNumberSize == 0) {
    return false;
  }
  rollNumber[0] = '\0';

  const size_t configuredUidLength = strlen(DEV_CARD_UID);
  const size_t configuredRollLength = strlen(DEV_CARD_ROLL_NUMBER);
  if (configuredUidLength == 0 || configuredRollLength == 0 ||
      configuredUidLength > 30 || configuredRollLength > 100 ||
      strcmp(uid, DEV_CARD_UID) != 0 ||
      configuredRollLength + 1 > rollNumberSize) {
    return false;
  }

  memcpy(rollNumber, DEV_CARD_ROLL_NUMBER, configuredRollLength + 1);
  return true;
}
