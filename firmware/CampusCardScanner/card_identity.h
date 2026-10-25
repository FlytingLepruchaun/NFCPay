#pragma once

#include <stddef.h>

bool readCardIdentity(const char* uid, char* rollNumber, size_t rollNumberSize);
