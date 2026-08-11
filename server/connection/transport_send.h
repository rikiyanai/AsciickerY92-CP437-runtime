#pragma once

// transport_send.h — Transport send primitives
//
// Thin abstraction over the socket write path.  Eventually absorbs
// Server::Send and the platform-specific send logic from game_app.cpp.

#include <stdint.h>
