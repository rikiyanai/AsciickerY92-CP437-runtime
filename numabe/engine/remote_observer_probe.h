#pragma once

#include <stdint.h>

struct Human;
struct Server;

bool RemoteObserverHasDeathEpoch(const Human* remote);
bool RemoteObserverProbePidValid(const Server* server, int pid);
void ResetRemoteObserverProbe(Server* server, int pid);
void RemoteObserverInitializeJoinState(Server* server, Human* remote, int pid);
void RemoteObserverNoteDeathSeq(Server* server, Human* remote, int pid, uint32_t source);
void RemoteObserverNoteRespawnSeq(Server* server, Human* remote, int pid);
void RemoteObserverNoteCorpseCreate(Server* server, Human* remote, int pid, uint32_t reason);
void RemoteObserverNoteCorpseDelete(Server* server, Human* remote, int pid, uint32_t reason);
